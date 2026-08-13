"""
ProviderHealthGate — Provider Health Gate (Phase X·Task1·创始人令 v3.4 出口稳定)
=============================================================================
目标: 任何 Agent 的 fallback provider 在进入链路前必须检查健康, 禁止静默失效。

禁止:
    unknown provider + invalid credential + silent fallback → 用户看到 502/401

检查项(进入链路前):
    - API key validity        (credential 有效性)
    - endpoint availability   (端点可达)
    - model availability      (模型可调)
    - latency                 (延迟)
    - capability compatibility(能力兼容)

输出: ProviderHealthEvent(进入 TrustEvent) + 健康状态判定(healthy/unhealthy/unknown)
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


HEALTHY = "healthy"
UNHEALTHY = "unhealthy"
UNKNOWN = "unknown"


@dataclass
class ProviderHealthEvent:
    """一次 provider 健康检查事件(TrustEvent 可负载)。"""
    provider: str
    model: str = ""
    status: str = UNKNOWN            # healthy/unhealthy/unknown
    api_key_valid: Optional[bool] = None
    endpoint_available: Optional[bool] = None
    model_available: Optional[bool] = None
    latency_ms: float = 0.0
    capability_compatible: Optional[bool] = None
    reason: str = ""
    ts: str = ""

    def to_trust_event(self) -> dict:
        return {
            "event": "ProviderHealth",
            "subtype": "RuntimeEvent",
            "domain": "provider",
            "provider": self.provider, "model": self.model, "status": self.status,
            "api_key_valid": self.api_key_valid, "endpoint_available": self.endpoint_available,
            "model_available": self.model_available, "latency_ms": self.latency_ms,
            "capability_compatible": self.capability_compatible, "reason": self.reason,
            "ts": self.ts,
        }


class ProviderHealthGate:
    """Provider 健康门: 检查 fallback provider 是否可用·禁止静默失效。"""

    def __init__(self):
        self._health: Dict[str, ProviderHealthEvent] = {}

    def check(self, provider: str, model: str = "",
              api_key_valid: Optional[bool] = None,
              endpoint_available: Optional[bool] = None,
              model_available: Optional[bool] = None,
              latency_ms: float = 0.0,
              capability_compatible: Optional[bool] = None,
              reason: str = "") -> ProviderHealthEvent:
        """检查一个 provider 的健康状态, 产生 ProviderHealthEvent。"""
        # 综合判定 status
        status = HEALTHY
        reasons = []
        if api_key_valid is False:
            status = UNHEALTHY; reasons.append("invalid api key")
        if endpoint_available is False:
            status = UNHEALTHY; reasons.append("endpoint unavailable")
        if model_available is False:
            status = UNHEALTHY; reasons.append("model unavailable")
        if capability_compatible is False:
            status = UNHEALTHY; reasons.append("capability incompatible")
        if api_key_valid is None and endpoint_available is None and model_available is None:
            status = UNKNOWN; reasons.append("not checked")

        ev = ProviderHealthEvent(
            provider=provider, model=model, status=status,
            api_key_valid=api_key_valid, endpoint_available=endpoint_available,
            model_available=model_available, latency_ms=latency_ms,
            capability_compatible=capability_compatible,
            reason=reason or ("; ".join(reasons) if reasons else "ok"),
            ts=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        self._health[provider] = ev
        return ev

    def is_healthy(self, provider: str) -> bool:
        """provider 是否健康(可进候选池)。"""
        ev = self._health.get(provider)
        return ev is not None and ev.status == HEALTHY

    def health(self, provider: str) -> Optional[ProviderHealthEvent]:
        return self._health.get(provider)

    def all(self) -> List[ProviderHealthEvent]:
        return list(self._health.values())
