from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from lao.effect_anchored.provider_health_gate import ProviderHealthGate
from ris.events import RuntimeHealthEvent


@dataclass
class ProviderIsolationEvent:
    """Provider 失效隔离事件桥接 dataclass，可转为 RuntimeHealthEvent。"""

    provider: str
    model: str = ""
    reason: str = ""
    severity: str = "error"
    agent_id: str = "system"
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_health_event(self) -> RuntimeHealthEvent:
        return RuntimeHealthEvent(
            event_type="provider_isolation",
            agent_id=self.agent_id,
            status="isolated",
            severity=self.severity,
            detail={
                "provider": self.provider,
                "model": self.model,
                "reason": self.reason,
            },
            ts=self.ts,
        )


class ProviderHealthMonitor:
    """Provider 失效隔离监控：不健康时输出 RuntimeHealthEvent 供 RIS 层处置。"""

    def __init__(self) -> None:
        self._gate = ProviderHealthGate()

    def monitor(
        self,
        provider: str,
        model: str = "",
        api_key_valid: Optional[bool] = None,
        endpoint_available: Optional[bool] = None,
        model_available: Optional[bool] = None,
    ) -> Optional[RuntimeHealthEvent]:
        """检查 provider 健康状态；不健康则返回 provider_isolation 事件，否则返回 None。"""
        event = self._gate.check(
            provider=provider,
            model=model,
            api_key_valid=api_key_valid,
            endpoint_available=endpoint_available,
            model_available=model_available,
        )
        if event.status == "unhealthy":
            return ProviderIsolationEvent(
                provider=provider,
                model=model,
                reason=event.reason,
            ).to_health_event()
        return None
