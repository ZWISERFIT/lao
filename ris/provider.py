"""RIS Provider Isolation — Provider 真实隔离/熔断 (B5 修复·2026-08-16)
=====================================================================================
审计缺陷(RIS-Audit-Report-20260816 §2.3/B3):
  provider_unavailable 事件产生后没有任何消费者执行隔离;成本事故链路
  (provider掉线→回退直连→单key混用→成本暴涨)只在事件里被"注释",没有被"阻断"。

本模块 = 真实隔离动作(状态落盘 + 事件产出 + LAO 可消费):
  1. 连续失败确认(CONFIRM_FAILURES 帧) — 防单次网络抖动误隔离(治审计 B10 抖动)
  2. 隔离状态持久化(provider-isolation.json·跨 30s 检测周期)
  3. 熔断冷却(ISOLATION_COOLDOWN_S) — 到期自动半开灰度回归,不会永久锁死
  4. 隔离指令进 ris-bridge.json summary.isolated_providers →
     LAO(lao_router_server._ris_guard_provider)读取后真实摘除候选池(治断头桥)

闭环链路: RIS 探活失败×N → isolate() 写状态+发 provider_isolation 事件 →
          bridge 同步 isolated_providers → LAO 阻断该 provider(降级切换) →
          冷却到期/探活恢复 → release() → LAO 灰度回归。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional

from ris.events import RuntimeHealthEvent

# 隔离状态落盘(共享状态约定·LAO/bridge 可直接读)
ISOLATION_FILE = "/home/agentuser/shared/state/provider-isolation.json"

CONFIRM_FAILURES = 2        # 连续失败确认帧数(防单帧抖动误隔离)
ISOLATION_COOLDOWN_S = 600  # 熔断 10 分钟·到期自动半开(灰度回归)


@dataclass
class ProviderIsolationEvent:
    """Provider 失效隔离事件桥接 dataclass，可转为 RuntimeHealthEvent。"""

    provider: str
    model: str = ""
    reason: str = ""
    severity: str = "error"
    agent_id: str = "system"
    status: str = "isolated"   # isolated / released
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_health_event(self) -> RuntimeHealthEvent:
        return RuntimeHealthEvent(
            event_type="provider_isolation",
            agent_id=self.agent_id,
            status=self.status,
            severity=self.severity,
            detail={
                "provider": self.provider,
                "model": self.model,
                "reason": self.reason,
            },
            ts=self.ts,
        )


class ProviderIsolator:
    """Provider 熔断器: 连续失败确认 → 隔离(写状态+发事件) → 冷却/恢复 → 释放。

    状态文件结构:
      { "<provider>": {"consecutive_failures": 0, "state": "open|isolated",
                        "isolated_at": ts, "isolated_until": ts,
                        "reason": "...", "last_event_ts": ts} }
    """

    def __init__(self, state_file: Optional[str] = None,
                 confirm_failures: int = CONFIRM_FAILURES,
                 cooldown_s: int = ISOLATION_COOLDOWN_S,
                 clock=time.time):
        # state_file=None → 运行时解析模块常量(测试可重定向·不写生产共享态)
        self.state_file = state_file or ISOLATION_FILE
        self.confirm_failures = confirm_failures
        self.cooldown_s = cooldown_s
        self._clock = clock

    # ── 状态读写 ──────────────────────────────────────────
    def _load(self) -> Dict:
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save(self, state: Dict) -> None:
        os.makedirs(os.path.dirname(self.state_file) or ".", exist_ok=True)
        tmp = self.state_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.state_file)

    def _now(self) -> float:
        return self._clock()

    # ── 失败/成功记账(由 RIS 主循环每帧调用) ───────────────
    def record_failure(self, provider: str, reason: str = "") -> Optional[RuntimeHealthEvent]:
        """探活失败记账: 连续 N 帧失败 → 隔离(状态跃迁时才发事件·防刷屏)。"""
        if not provider:
            return None
        state = self._load()
        now = self._now()
        st = state.get(provider) or {"consecutive_failures": 0, "state": "open"}
        st["consecutive_failures"] = int(st.get("consecutive_failures", 0)) + 1
        st["last_reason"] = reason or st.get("last_reason", "")
        st["last_event_ts"] = now

        event = None
        already_isolated = st.get("state") == "isolated" and now < st.get("isolated_until", 0)
        if not already_isolated and st["consecutive_failures"] >= self.confirm_failures:
            st["state"] = "isolated"
            st["isolated_at"] = now
            st["isolated_until"] = now + self.cooldown_s
            st["reason"] = st["last_reason"]
            event = ProviderIsolationEvent(
                provider=provider,
                reason=f"consecutive_failures={st['consecutive_failures']}: {st['reason']}",
            ).to_health_event()
            event.detail["isolated_until"] = st["isolated_until"]
            event.detail["cooldown_s"] = self.cooldown_s
        state[provider] = st
        self._save(state)
        return event

    def record_success(self, provider: str) -> Optional[RuntimeHealthEvent]:
        """探活成功记账: 隔离中的 provider 恢复 → 提前释放(灰度回归)。

        未隔离时仅清零失败计数(不产生事件·不刷日志)。
        """
        if not provider:
            return None
        state = self._load()
        st = state.get(provider)
        if not st:
            return None
        now = self._now()
        was_isolated = st.get("state") == "isolated"
        st["consecutive_failures"] = 0
        st["state"] = "open"
        st["released_at"] = now
        state[provider] = st
        self._save(state)
        if was_isolated:
            return ProviderIsolationEvent(
                provider=provider,
                severity="info",
                status="released",
                reason="probe recovered → re-admit (gray re-entry)",
            ).to_health_event()
        return None

    # ── 查询(LAO/bridge 消费) ─────────────────────────────
    def is_isolated(self, provider: str, now: Optional[float] = None) -> bool:
        """provider 当前是否处于隔离(冷却到期自动视为半开·可回归)。"""
        now = now if now is not None else self._now()
        st = self._load().get(provider)
        return bool(st and st.get("state") == "isolated"
                    and now < st.get("isolated_until", 0))

    def active(self) -> Dict[str, Dict]:
        """当前活跃隔离指令(供 bridge summary.isolated_providers / LAO 消费)。"""
        now = self._now()
        out: Dict[str, Dict] = {}
        for provider, st in self._load().items():
            if st.get("state") == "isolated" and now < st.get("isolated_until", 0):
                out[provider] = {
                    "isolated_at": st.get("isolated_at"),
                    "isolated_until": st.get("isolated_until"),
                    "reason": st.get("reason", ""),
                }
        return out


__all__ = ["ProviderIsolator", "ProviderIsolationEvent",
           "ISOLATION_FILE", "CONFIRM_FAILURES", "ISOLATION_COOLDOWN_S"]
