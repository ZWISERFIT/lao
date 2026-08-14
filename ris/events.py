"""
RIS — RuntimeHealthEvent（运行健康事件）

第三阶段：TrustEvent 体系更新
  LAO 输出：TrustEvent（认知可靠：验证/锚点/经验/确权）
  RIS 输出：RuntimeHealthEvent（运行可靠：健康/恢复/监控/隔离）

RuntimeHealthEvent 是 RIS 层的独立事件类型，从原 TrustEvent 的 subtype=RuntimeEvent
提升为独立顶层事件，标识运行免疫层（RIS）与认知可靠层（LAO）的事件分离。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional


@dataclass
class RuntimeHealthEvent:
    """RIS 运行免疫层输出的健康事件。

    承载 5 类运行健康信号（对应 RIS 5 tests）：
      - session_recovery   会话故障自动恢复
      - gateway_recovery   网关异常恢复
      - cpu_anomaly        CPU 异常检测
      - provider_isolation provider 失效隔离
      - config_drift       config drift 检测
    """
    event_type: str          # session_recovery / gateway_recovery / cpu_anomaly / provider_isolation / config_drift
    agent_id: str
    status: str              # detected / recovering / recovered / isolated / failed
    severity: str = "info"   # info / warn / error / critical
    detail: Dict = field(default_factory=dict)
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    layer: str = "ris"       # 运行免疫层标识（与 to_dict 一致）

    def to_dict(self) -> dict:
        return {
            "layer": "ris",
            "event_type": self.event_type,
            "agent_id": self.agent_id,
            "status": self.status,
            "severity": self.severity,
            "detail": self.detail,
            "ts": self.ts,
        }


# ── RIS 事件类型清单（对应 5 类运行健康信号）──
RIS_EVENT_TYPES = (
    "session_recovery",      # 会话故障自动恢复
    "gateway_recovery",      # 网关异常恢复
    "cpu_anomaly",           # CPU 异常检测
    "provider_isolation",    # provider 失效隔离
    "config_drift",          # config drift 检测
)

__all__ = ["RuntimeHealthEvent", "RIS_EVENT_TYPES"]
