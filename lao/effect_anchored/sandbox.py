"""
LAOSandbox — External Developer Sandbox (Phase2 P0-5·创始人令 v3.4)
=============================================================================
杀手级体验: 开发者第一天就能"故意弄坏 Agent，然后看 LAO 自动修"。

能力(创始人 P0-5):
- ① Agent 模拟     : 注册模拟 Agent(RuntimeRegistry)
- ② 故障注入       : FailureInjector(mock status/failure domain)
- ③ 恢复测试       : 串 Phase1 RecoveryGate + RecoveryVerifier(自动恢复闭环)
- ④ TrustEvent 查看: 暴露事件流(可审计)
- ⑤ Experience生成 : ExperienceAsset 基础(衔接 P0-3/P0-4)

设计原则(闭环):
    Failure(Integer) → Detect(RuntimeRegistry) → Evidence(TrustEvent)
    → Decision(RecoveryGate) → Action(RecoveryVerifier) → Verification(verify)
    → Experience(Asset)

开发者视角 = 可复现的"弄坏→自愈→证明"演示。
"""
from __future__ import annotations
import json, time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from lao.effect_anchored.runtime_registry import RuntimeRegistry
from lao.effect_anchored.recovery_budget import RecoveryGate, RecoveryBudget
from lao.effect_anchored.recovery_verifier import RecoveryVerifier
from lao.effect_anchored.failure_domain import FailureDomainDetector, AgentSignal


@dataclass
class SandboxScenario:
    """一个"演示剧本": 弄坏→自愈→证明。"""
    name: str
    agent: str
    failure_domain: str = "gateway"
    inject_status: str = "recovering"
    health_check_fn: Optional[Callable[[], bool]] = None      # 模拟探测
    synthetic_task_fn: Optional[Callable[[], Any]] = None     # 模拟最小任务
    recovery_budget: int = 3
    steps: List[dict] = field(default_factory=list)
    verified: bool = False
    attestation: str = ""
    ts: str = ""


class FailureInjector:
    """故障注入器: 把一个健康 Agent 弄坏(Detect 阶段)。"""

    def __init__(self, registry: RuntimeRegistry):
        self._reg = registry

    def inject(self, agent: str, domain: str = "gateway", status: str = "recovering") -> dict:
        """把 Agent 状态改为故障(recovering/degraded/offline)。"""
        st = self._reg.set_status(agent, status, domain=domain,
                                  recovery_state="attempt 0/0")
        ev = {
            "event": "FailureInjected",
            "subtype": "RuntimeEvent",
            "domain": domain,
            "agent": agent,
            "status": status,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        return ev if st else ev

    def inject_multi(self, domains: Dict[str, str]) -> List[dict]:
        """多 Agent 同时注入(演示 Correlated Failure)。"""
        return [self.inject(a, d) for a, d in domains.items()]


class LAOSandbox:
    """External Developer Sandbox: 日常演示闭环。"""

    def __init__(self, registry: Optional[RuntimeRegistry] = None):
        self.registry = registry or RuntimeRegistry()
        self.injector = FailureInjector(self.registry)
        self.detector = FailureDomainDetector()
        self.event_log: List[dict] = []
        self.scenarios: List[SandboxScenario] = []

    def _log(self, ev: dict):
        self.event_log.append(ev)
        return ev

    # ① 注册 Agent(模拟)
    def register_agents(self, agents: List[str]) -> None:
        for a in agents:
            self.registry.register(a, did=f"did:zwf:{a}")
        self._log({"event": "AgentsRegistered", "subtype": "RuntimeEvent",
                   "agents": agents, "ts": _now()})

    # ②+③ 运行一个故障→自愈场景
    def run_heal_demo(self, agent: str, domain: str = "gateway",
                      health_ok: bool = True, model_ok: bool = True) -> SandboxScenario:
        """一次完整的"弄坏→自动修→证明"演示。"""
        sc = SandboxScenario(name=f"{agent}-{domain}", agent=agent,
                             failure_domain=domain,
                             health_check_fn=lambda ok=health_ok: ok,
                             synthetic_task_fn=lambda ok=model_ok: "OK" if ok else "")
        self.scenarios.append(sc)

        # Detect: 注入故障
        self._log(self.injector.inject(agent, domain))
        sc.steps.append({"phase": "detect", "action": f"inject failure domain={domain}"})

        # Decision: RecoveryGate
        gate = RecoveryGate(RecoveryBudget(max_attempts=sc.recovery_budget))
        decision = gate.check_before_recovery()
        sc.steps.append({"phase": "decision", "action": decision.action, "reason": decision.reason})
        self._log({**decision.to_trust_event(), "agent": agent})

        # Action + Verification: RecoveryVerifier
        rv = RecoveryVerifier()
        v = rv.create(f"rec-{agent}-{int(time.time())}", f"auto_heal_{domain}")
        rv.add_health_check(v, "port_probe", sc.health_check_fn, "18789")
        rv.add_health_check(v, "http_health", lambda: health_ok, "/v1/models")
        rv.set_synthetic_task(v, "model_ping", sc.synthetic_task_fn, "non_empty")
        rv.verify(v, execution="success", agent_response="I'm back online" if model_ok else "")

        sc.steps.append({"phase": "action+verify",
                         "execution": v.execution, "verified": v.verified,
                         "runtime_health": v.runtime_health, "agent_health": v.agent_health})
        sc.verified = v.verified
        sc.attestation = v.attestation
        self._log({**v.to_trust_event(), "agent": agent})

        # 恢复成功 → 状态更新
        if v.verified:
            self.registry.set_status(agent, "online", domain="")
            self.registry.record_success(agent)
        sc.ts = _now()
        return sc

    # ④ TrustEvent 查看
    def trust_events(self) -> List[dict]:
        return self.event_log

    # ⑤ Experience 基础(衔接 P0-3)
    def last_experience_asset(self, scenario: SandboxScenario) -> dict:
        """从最近一次成功自愈生成 ExperienceAsset 雏形。"""
        return {
            "asset_id": f"EXP-{len(self.scenarios):05d}",
            "creator_did": "did:zwf:developer",
            "problem": f"{scenario.agent} {scenario.failure_domain} failure",
            "solution": f"auto_heal_{scenario.failure_domain}",
            "verification_pct": 100 if scenario.verified else 0,
            "attestation": scenario.attestation,
            "ts": scenario.ts,
        }


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")
