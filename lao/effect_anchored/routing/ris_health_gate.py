"""LAO 侧 RIS 健康门 (B2 修复·2026-08-16)
=====================================================================================
审计缺陷(RIS-Audit-Report-20260816 §3.2-1/B3):
  ris-bridge.json / ris_summary.json 全仓库零消费者——LAO 路由决策对 RIS 检测到的
  provider 掉线/隔离/成本风险完全无感, "provider掉线→回退直连→成本暴涨"的
  成本事故链路只被 RIS 注释、没有被 LAO 阻断。

本模块 = 桥的消费端(LAO 真正读取 RIS):
  1. 读 /home/agentuser/shared/state/ris-bridge.json:
       summary.provider_status[p]=="down"  → 阻断
       summary.isolated_providers          → 阻断(B5 熔断指令)
  2. 读 ris/experience/data/ris_summary.json(第二桥的真实消费者):
       active_risks 中 provider_unavailable → 阻断对应 provider
  3. 陈旧保护(fail-open): 桥文件超过 STALE_S 未更新(RIS 挂了) → 不阻断,
     避免 RIS 故障放大为 LAO 全停(单层故障不跨层传播)。

由 lao_router_server._ris_guard_provider() 在每次路由转发前调用:
  被 RIS 阻断的 provider → 摘出候选池, 降级切换到健康 provider;
  全部候选被阻断 → 显式 503(禁止静默 fallback·对齐 ProviderHealthGate 哲学)。
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, Optional, Set

# ── 桥文件(与 ris.bridge / ris.experience_integration 的写入端对齐) ──
RIS_BRIDGE_FILE = "/home/agentuser/shared/state/ris-bridge.json"
RIS_SUMMARY_FILE = "/home/agentuser/lao-release/ris/experience/data/ris_summary.json"

# 桥陈旧阈值: RIS 每 30s 同步·超过 180s 未更新视为陈旧(RIS 停摆)→ fail-open
RIS_BRIDGE_STALE_S = 180


class RISHealthGate:
    """LAO → RIS 单向读取门: provider 健康状态/隔离指令/风险信号。"""

    def __init__(self, bridge_file: Optional[str] = None,
                 summary_file: Optional[str] = None,
                 stale_s: float = RIS_BRIDGE_STALE_S):
        # None → 运行时解析模块常量(测试可重定向)
        self.bridge_file = bridge_file or RIS_BRIDGE_FILE
        self.summary_file = summary_file or RIS_SUMMARY_FILE
        self.stale_s = stale_s
        self._cache: Optional[Dict] = None
        self._cache_at = 0.0

    def _read_fresh(self, path: str) -> Optional[Dict]:
        """读取 JSON; 不存在/损坏/陈旧(mtime 超时) → None。"""
        try:
            if time.time() - os.stat(path).st_mtime > self.stale_s:
                return None
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def read(self, force: bool = False) -> Dict:
        """聚合两座桥的阻断信号(30s 内复用缓存)。

        Returns: {"blocked": set, "provider_status": dict,
                  "fresh": bool, "source": str}
        """
        now = time.time()
        if not force and self._cache is not None and now - self._cache_at < 30:
            return self._cache

        blocked: Set[str] = set()
        status: Dict[str, str] = {}
        sources = []

        bridge = self._read_fresh(self.bridge_file)
        if bridge:
            sources.append("ris-bridge")
            s = bridge.get("summary", {})
            for p, st in (s.get("provider_status") or {}).items():
                status[p] = st
                if st == "down":
                    blocked.add(str(p).lower())
            for p in s.get("isolated_providers") or []:
                blocked.add(str(p).lower())
                status.setdefault(str(p).lower(), "isolated")

        summary = self._read_fresh(self.summary_file)
        if summary:
            sources.append("ris_summary")
            for risk in summary.get("active_risks") or []:
                if risk.get("event_type") != "provider_unavailable":
                    continue
                d = risk.get("detail") or {}
                p = str(d.get("provider") or risk.get("agent_id") or "").lower()
                if p:
                    blocked.add(p)
                    status[p] = "down"

        snap = {"blocked": blocked, "provider_status": status,
                "fresh": bool(sources), "source": "+".join(sources) or "none(fail-open)"}
        self._cache, self._cache_at = snap, now
        return snap

    def is_blocked(self, provider: str) -> bool:
        return (provider or "").lower() in self.read()["blocked"]


__all__ = ["RISHealthGate", "RIS_BRIDGE_FILE", "RIS_SUMMARY_FILE",
           "RIS_BRIDGE_STALE_S"]
