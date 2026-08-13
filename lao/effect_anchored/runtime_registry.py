"""
RuntimeRegistry — Agent Runtime Registry (Phase2 P0-1·创始人令 v3.4)
=============================================================================
最大体验问题: 用户不知道 Agent 是否活着。

RuntimeRegistry 让外部开发者实时看到每个 Agent 运行状态(第一印象·五秒惊叹):

    RuntimeRegistry
        agent_id, did, status, health, cpu, memory, context_usage,
        last_success, last_failure, current_model, provider, recovery_state

外部体验视图:
    Stella  ✓ online   DeepSeek v4 flash  Latency 2.1s  Trust 98
    Zeus    ⚠ recovering  Failure domain: gateway  Recovery 1/3

闭环接入(6阶段·创始人):
    Register/Heartbeat(Detect) → StatusEvent(Evidence) → Decision
    → Action → Verify(Verification) → Experience

单一事实源: AgentRuntimeState 变更 → TrustEvent(status/subtype=RuntimeEvent)
不另建事实账本。
"""
from __future__ import annotations
import time, threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class AgentRuntimeState:
    """单个 Agent 的实时运行状态。"""
    agent_id: str
    did: str = ""
    status: str = "unknown"          # online / recovering / offline / degraded
    health: str = "unknown"          # healthy / degraded / unhealthy
    cpu: float = 0.0                 # %
    memory_mb: float = 0.0
    context_usage: float = 0.0       # 0.0~1.0
    last_success: str = ""           # ISO ts
    last_failure: str = ""
    current_model: str = ""
    provider: str = ""
    latency_ms: float = 0.0
    recovery_state: str = ""         # 如 "attempt 1/3" / "safe_mode"
    failure_domain: str = ""         # FailureDomain(Phase1·gateway/network/...)
    trust_score: float = 0.0         # 0-100

    def to_row(self, indexed: bool = False) -> dict:
        return {
            "agent_id": self.agent_id, "did": self.did, "status": self.status,
            "health": self.health, "cpu": self.cpu, "memory_mb": self.memory_mb,
            "context_usage": self.context_usage, "last_success": self.last_success,
            "last_failure": self.last_failure, "current_model": self.current_model,
            "provider": self.provider, "latency_ms": self.latency_ms,
            "recovery_state": self.recovery_state, "failure_domain": self.failure_domain,
            "trust_score": self.trust_score,
        }


class RuntimeRegistry:
    """Agent Runtime Registry(内存 + TrustEvent 持久化追踪)。"""

    def __init__(self):
        self._agents: Dict[str, AgentRuntimeState] = {}
        self._lock = threading.Lock()

    def register(self, agent_id: str, did: str = "", model: str = "", provider: str = "") -> AgentRuntimeState:
        """注册一个 Agent(Detect 阶段·idempotent)。"""
        with self._lock:
            if agent_id not in self._agents:
                self._agents[agent_id] = AgentRuntimeState(
                    agent_id=agent_id, did=did, current_model=model, provider=provider)
            st = self._agents[agent_id]
            st.status = "online" if st.status == "unknown" else st.status
            return st

    def get(self, agent_id: str) -> Optional[AgentRuntimeState]:
        with self._lock:
            return self._agents.get(agent_id)

    def set_status(self, agent_id: str, status: str, domain: str = "",
                   recovery_state: str = "", **kw) -> Optional[AgentRuntimeState]:
        """更新状态(Evidence→Decision 输入)。"""
        with self._lock:
            st = self._agents.get(agent_id)
            if not st:
                st = self.register(agent_id)
            st.status = status
            if domain:
                st.failure_domain = domain
            if recovery_state is not None:
                st.recovery_state = recovery_state
            if status == "online":
                st.health = "healthy"
            elif status == "degraded":
                st.health = "degraded"
            elif status in ("recovering", "offline"):
                st.health = "unhealthy"
            for k, v in kw.items():
                if hasattr(st, k) and v is not None:
                    setattr(st, k, v)
            return st

    def record_success(self, agent_id: str, latency_ms: float = 0.0) -> None:
        with self._lock:
            st = self._agents.get(agent_id) or self.register(agent_id)
            st.last_success = _now()
            st.latency_ms = latency_ms
            st.trust_score = min(100.0, st.trust_score + 1.0)

    def record_failure(self, agent_id: str, domain: str = "") -> None:
        with self._lock:
            st = self._agents.get(agent_id) or self.register(agent_id)
            st.last_failure = _now()
            st.trust_score = max(0.0, st.trust_score - 3.0)
            if domain:
                st.failure_domain = domain

    def all(self) -> List[AgentRuntimeState]:
        with self._lock:
            return list(self._agents.values())

    def summary(self) -> Dict[str, int]:
        """全 Agent 状态统计(外部体验·期望9 vs 观测N)。"""
        with self._lock:
            return {
                "total": len(self._agents),
                "online": sum(1 for a in self._agents.values() if a.status == "online"),
                "recovering": sum(1 for a in self._agents.values() if a.status == "recovering"),
                "degraded": sum(1 for a in self._agents.values() if a.status == "degraded"),
                "offline": sum(1 for a in self._agents.values() if a.status == "offline"),
            }


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")
