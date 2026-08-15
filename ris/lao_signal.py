"""LAO→RIS 反向桥消费端 (B2 修复·2026-08-16)
=====================================================================================
审计缺陷(RIS-Audit-Report-20260816 §3.2-2):
  LAO 内部高价值信号(路由成功率/错误率/cache miss/成本水位)没有任何一条流入 RIS,
  RIS 对 LAO 的认知只有自己拿 HTTP 探针戳端口——"错误率升高、cache miss 暴涨、
  预算穿透这些 LAO 一手掌握的退化信号, RIS 全盲"。

反向桥链路(与 ris.bridge.py 的 RIS→LAO 方向对称):
  lao_router_server._update_lao_signal()  ──写──►  /home/agentuser/shared/state/lao-signal.json
                                                        │ (滚动窗口 50 条/provider)
  LAOSignalMonitor.check_once()           ◄──读───────┘
                                                        ▼
  错误率超阈 → provider_unavailable(source=lao-signal) 事件
            → ProviderIsolator.record_failure → 隔离指令进 ris-bridge
            → LAO 真实摘除该 provider  → 双向数据飞轮闭环
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional

from ris.events import RuntimeHealthEvent

LAO_SIGNAL_FILE = "/home/agentuser/shared/state/lao-signal.json"

SIGNAL_STALE_S = 300        # 信号超过 5 分钟未更新 → 视为陈旧(LAO 停写·不消费)
MIN_REQUESTS = 5            # 窗口内至少 5 次请求才评估错误率(小样本不判定)
ERROR_RATE_THRESHOLD = 0.3  # 窗口错误率 ≥30% → provider 退化事件


class LAOSignalMonitor:
    """消费 LAO 反向桥信号, 产出 error-rate 类 provider 退化事件。

    补齐审计 B10 数据源: 探活只覆盖"可达性", 本模块把 LAO 一手的
    转发错误率/缓存命中率/成本水位转成 RIS 可处置的运行健康事件。
    """

    def __init__(self, signal_file: Optional[str] = None,
                 stale_s: float = SIGNAL_STALE_S,
                 min_requests: int = MIN_REQUESTS,
                 error_rate_threshold: float = ERROR_RATE_THRESHOLD):
        # None → 运行时解析模块常量(测试可重定向)
        self.signal_file = signal_file or LAO_SIGNAL_FILE
        self.stale_s = stale_s
        self.min_requests = min_requests
        self.error_rate_threshold = error_rate_threshold

    def read(self) -> Optional[Dict]:
        """读取信号文件; 不存在或陈旧(mtime 超时)返回 None(fail-open·不误报)。"""
        if not os.path.exists(self.signal_file):
            return None
        try:
            # mtime 陈旧判定(LAO 每 30s 更新·stale_s 内视为有效)
            if abs(time.time() - os.stat(self.signal_file).st_mtime) > self.stale_s:
                return None
            with open(self.signal_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def check_once(self) -> List[RuntimeHealthEvent]:
        """评估各 provider 窗口: 错误率超阈 → provider_unavailable(lao-signal 源)。"""
        signal = self.read()
        if not signal:
            return []
        events: List[RuntimeHealthEvent] = []
        providers = (signal.get("window") or {}).get("providers") or {}
        for provider, stats in providers.items():
            n = int(stats.get("requests", 0))
            err_rate = float(stats.get("error_rate", 0.0))
            if n < self.min_requests or err_rate < self.error_rate_threshold:
                continue
            sev = "critical" if err_rate >= 0.5 else "error"
            events.append(RuntimeHealthEvent(
                event_type="provider_unavailable",
                agent_id=provider,
                status="detected",
                severity=sev,
                detail={
                    "provider": provider,
                    "source": "lao-signal",
                    "reason": (f"error_rate {err_rate:.0%} >= "
                               f"{self.error_rate_threshold:.0%} over {n} req (rolling window)"),
                    "requests": n,
                    "errors": int(stats.get("errors", 0)),
                    "error_rate": err_rate,
                    "cache_hit_rate": stats.get("cache_hit_rate"),
                    "cost_usd": stats.get("cost_usd", 0.0),
                    "cost_impact": "high" if sev == "critical" else "medium",
                    "fallback_target": None,
                },
            ))
        return events


__all__ = ["LAOSignalMonitor", "LAO_SIGNAL_FILE",
           "SIGNAL_STALE_S", "MIN_REQUESTS", "ERROR_RATE_THRESHOLD"]
