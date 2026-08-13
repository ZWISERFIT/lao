"""
RecoveryVerifier — Recovery Verification (P0-5 · 创始人终审 Phase1 Step3)
=============================================================================
创始人/ChatGPT 核心断言: **Restart ≠ Recovery**。

一个 Restart 动作执行成功, 不代表 Agent 恢复可用。
真正的 Recovery 必须证明:
    Recovery = Action 执行
             + Health Check(端口/HTTP/心跳)
             + Synthetic Task(最小真实任务·模型调用必须成功)
             + Agent Response(能返回可用结果)
             + Attestation(可验证·TrustEvent)

信任原则(ChatGPT/founder):
- 不信任"执行成功" → 必须过 Health + Synthetic Task
- 不信任"自报健康" → Attestation + TrustEvent 独立可验证
- Restart 是动作, Recovery 是状态; 只有 verification.verified=True 才算恢复。
"""
from __future__ import annotations
import json, os, time, logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("lao.recovery.verifier")


@dataclass
class HealthCheck:
    """一项健康检查(可插拔·由 Runtime Adapter 决定具体探测方式)。"""
    name: str                      # port_probe / http_health / heartbeat / session / agent
    check_fn: Optional[Callable[[], bool]] = None   # 实际探测(OpenClaw/Hermes Adapter 注入)
    detail: str = ""
    result: Optional[bool] = None   # None=未跑

    def run(self) -> bool:
        if self.check_fn is None:
            self.result = None
            return False
        try:
            self.result = bool(self.check_fn())
        except Exception as e:
            self.result = False
            self.detail = f"err:{e}"
        return self.result


@dataclass
class SyntheticTask:
    """Synthetic Task: 最小真实任务(模型调用必须成功·证明 Agent 可工作)。"""
    name: str
    task_fn: Optional[Callable[[], Any]] = None      # 执行最小任务(如调一次模型)
    expect: str = "non_empty"                        # 判定: non_empty / contains / ==
    result: Any = None
    passed: Optional[bool] = None
    detail: str = ""

    def run(self) -> bool:
        if self.task_fn is None:
            self.passed = None
            return False
        try:
            self.result = self.task_fn()
            if self.expect == "non_empty":
                self.passed = bool(self.result is not None and str(self.result).strip() != "")
            elif self.expect == "contains":
                self.passed = self.expect in str(self.result)
            else:
                self.passed = self.result == self.expect
        except Exception as e:
            self.passed = False
            self.detail = f"err:{e}"
        return self.passed


@dataclass
class RecoveryVerification:
    """Recovery 验证结果(TrustEvent 可负载)。"""
    recovery_id: str
    action: str = ""                 # 恢复动作(restart_gateway / fallback / rebuild ...)
    execution: str = ""              # 动作执行状态(success / failed)
    health_checks: List[HealthCheck] = field(default_factory=list)
    synthetic_task: Optional[SyntheticTask] = None
    agent_response: str = ""         # 恢复后 Agent 能返回的结果
    runtime_health: str = "unknown"
    agent_health: str = "unknown"
    synthetic_task_passed: bool = False
    verified: bool = False
    attestation: str = ""            # 可验证 attestation id / 指纹
    ts: str = ""

    def all_health_ok(self) -> bool:
        return bool(self.health_checks) and all(h.result for h in self.health_checks)

    def evaluate(self) -> "RecoveryVerification":
        """按 ChatGPT/founder 标准判定 Recovery 是否真正成立。"""
        self.runtime_health = "healthy" if self.all_health_ok() else "degraded"
        self.synthetic_task_passed = bool(self.synthetic_task and self.synthetic_task.passed)
        self.agent_health = "healthy" if self.synthetic_task_passed else "unhealthy"
        # 核心: 执行成功 + 全部健康 + synthetic task 通过 + 有响应 → 才算 verified
        self.verified = (
            self.execution == "success"
            and self.all_health_ok()
            and self.synthetic_task_passed
            and bool(self.agent_response)
        )
        return self

    def to_trust_event(self) -> Dict[str, Any]:
        """→ TrustEvent 负载(评估后·可写 ledger·公开可验证)。"""
        return {
            "event": "RecoveryVerified" if self.verified else "RecoveryFailedOrUnverified",
            "subtype": "RecoveryEvent",
            "domain": "runtime",
            "recovery_id": self.recovery_id,
            "action": self.action,
            "execution": self.execution,
            "runtime_health": self.runtime_health,
            "agent_health": self.agent_health,
            "synthetic_task": self.synthetic_task.name if self.synthetic_task else "",
            "synthetic_task_passed": self.synthetic_task_passed,
            "agent_response_present": bool(self.agent_response),
            "verified": self.verified,
            "attestation": self.attestation,
            "ts": self.ts,
        }


class RecoveryVerifier:
    """Recovery 验证引擎: Close the loop (Restart → 证明 Recovery)。

    - execution 由调用方填入(RecoveryAction 执行结果)
    - health_checks / synthetic_task 为可插拔(OpenClaw/Hermes Adapter 注入)
    - verify() 跑全量检查 → evaluate → attestation → verified 判定
    """

    def __init__(self, attestation_fn: Optional[Callable[[Dict[str, Any]], str]] = None):
        self._attestation_fn = attestation_fn  # 可注入: 生成 attestation id/指纹

    def create(self, recovery_id: str, action: str = "") -> RecoveryVerification:
        return RecoveryVerification(recovery_id=recovery_id, action=action,
                                    ts=time.strftime("%Y-%m-%dT%H:%M:%S%z"))

    def add_health_check(self, v: RecoveryVerification, name: str, check_fn=None, detail=""):
        v.health_checks.append(HealthCheck(name=name, check_fn=check_fn, detail=detail))
        return v

    def set_synthetic_task(self, v: RecoveryVerification, name: str, task_fn=None, expect="non_empty"):
        v.synthetic_task = SyntheticTask(name=name, task_fn=task_fn, expect=expect)
        return v

    def verify(self, v: RecoveryVerification, execution: str = "success",
               agent_response: str = "") -> RecoveryVerification:
        """执行全部检查 → 评估 → 加 attestation(TrustEvent 可验证)。"""
        v.execution = execution
        v.agent_response = agent_response or ""
        for hc in v.health_checks:
            if hc.result is None:
                hc.run()
        if v.synthetic_task and v.synthetic_task.passed is None:
            v.synthetic_task.run()
        v.evaluate()
        if self._attestation_fn:
            try:
                v.attestation = self._attestation_fn(v.to_trust_event())
            except Exception as e:
                v.attestation = f"attest_err:{e}"
        else:
            v.attestation = "sha256:" + _simple_fp(json.dumps(v.to_trust_event(), default=str))
        return v


def _simple_fp(payload: str) -> str:
    import hashlib
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def make_gateway_recovery_verifier(port_check=None, http_check=None, model_task=None,
                                   attestation_fn=None):
    """构造面向 gateway 恢复的验证器(Test 用模板)。"""
    rv = RecoveryVerifier(attestation_fn=attestation_fn)
    v = rv.create(recovery_id="rec-gw-0001", action="restart_gateway")
    rv.add_health_check(v, "port_probe", port_check, "18789")
    rv.add_health_check(v, "http_health", http_check, "/v1/models")
    rv.set_synthetic_task(v, "model_ping", model_task, "non_empty")
    return rv, v
