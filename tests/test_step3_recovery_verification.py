"""Phase1 Step3 测试: Recovery Verification (Restart ≠ Recovery)。

创始人终审 2026-08-13 P0-5: 恢复必须证明, 不只执行。
Recovery = Action + HealthCheck + SyntheticTask + AgentResponse + Attestation。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.recovery_verifier import (
    RecoveryVerifier, make_gateway_recovery_verifier, HealthCheck, SyntheticTask,
)


def test_restart_success_but_unhealthy_is_not_recovery():
    """核心断言: restart 执行成功但 health 全挂 → 不是 Recovery。"""
    rv = RecoveryVerifier()
    v = rv.create("rec-t-001", "restart_gateway")
    rv.add_health_check(v, "port_probe", lambda: False, "18789")
    rv.add_health_check(v, "http_health", lambda: False, "/v1/models")
    rv.set_synthetic_task(v, "model_ping", lambda: None)
    rv.verify(v, execution="success", agent_response="")
    assert v.verified is False
    assert v.runtime_health == "degraded"
    assert v.agent_health == "unhealthy"


def test_full_recovery_is_verified():
    """完整恢复(port+http+synthetic模型+响应) → verified。"""
    rv, v = make_gateway_recovery_verifier(
        port_check=lambda: True, http_check=lambda: True, model_task=lambda: "OK")
    rv.verify(v, execution="success", agent_response="hello")
    assert v.verified is True
    assert v.runtime_health == "healthy"
    assert v.agent_health == "healthy"
    assert v.synthetic_task_passed is True


def test_agent_response_required():
    """即使 health+synthetic 通过, 无 agent 响应 → 不 verified(不信任自报)。"""
    rv, v = make_gateway_recovery_verifier(
        port_check=lambda: True, http_check=lambda: True, model_task=lambda: "OK")
    rv.verify(v, execution="success", agent_response="")   # 无响应
    assert v.verified is False  # 缺 agent_response

def test_trust_event_emitted():
    """验证结果必须产出 TrustEvent 负载(可审计·subtype/attestation)。"""
    rv, v = make_gateway_recovery_verifier(
        port_check=lambda: True, http_check=lambda: True, model_task=lambda: "OK")
    rv.verify(v, execution="success", agent_response="recovered")
    te = v.to_trust_event()
    assert te["subtype"] == "RecoveryEvent"
    assert te["event"] == "RecoveryVerified"
    assert te["verified"] is True
    assert te["attestation"].startswith("sha256:")
