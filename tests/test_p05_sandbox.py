"""Phase2 P0-5 测试: External Developer Sandbox (故意弄坏→自动修→证明)。

创始人 v3.4 P0-5: 杀手级体验 — 开发者第一天"故意弄坏 Agent, 看 LAO 自动修"。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.sandbox import LAOSandbox, FailureInjector


def test_register_agents():
    """注册模拟 Agent(RuntimeRegistry 复用)。"""
    sandbox = LAOSandbox()
    sandbox.register_agents(["stella", "zeus"])
    assert sandbox.registry.summary()["total"] == 2


def test_inject_failure():
    """故障注入(recovering/gateway)。"""
    sandbox = LAOSandbox()
    sandbox.register_agents(["stella"])
    ev = sandbox.injector.inject("stella", domain="gateway")
    assert ev["event"] == "FailureInjected"
    assert sandbox.registry.get("stella").status == "recovering"
    assert sandbox.registry.get("stella").failure_domain == "gateway"


def test_heal_demo_success():
    """健康恢复场景 → verified=True → agent 回 online。"""
    sandbox = LAOSandbox()
    sandbox.register_agents(["stella"])
    sc = sandbox.run_heal_demo("stella", "gateway", health_ok=True, model_ok=True)
    assert sc.verified is True
    assert sandbox.registry.get("stella").status == "online"
    assert sc.attestation.startswith("sha256:")


def test_heal_demo_cannot_fake_recovery():
    """health 挂 → verified=False(不能假装痊愈)。"""
    sandbox = LAOSandbox()
    sandbox.register_agents(["zeus"])
    sc = sandbox.run_heal_demo("zeus", "provider", health_ok=False, model_ok=False)
    assert sc.verified is False
    assert sandbox.registry.get("zeus").status == "recovering"


def test_trust_events_auditable():
    """闭环必须产出 TrustEvent 证据(可审计·含 RecoveryEvent)。"""
    sandbox = LAOSandbox()
    sandbox.register_agents(["nova"])
    sandbox.run_heal_demo("nova", "gateway", health_ok=True, model_ok=True)
    events = sandbox.trust_events()
    assert any(e["event"] in ("RecoveryVerified", "RecoveryFailedOrUnverified") for e in events)
    assert any(e["event"] == "FailureInjected" for e in events)


def test_experience_asset_generated():
    """从成功自愈生成 ExperienceAsset(衔接 P0-3)。"""
    sandbox = LAOSandbox()
    sandbox.register_agents(["stella"])
    sc = sandbox.run_heal_demo("stella", "gateway", health_ok=True, model_ok=True)
    asset = sandbox.last_experience_asset(sc)
    assert asset["asset_id"].startswith("EXP-")
    assert asset["verification_pct"] == 100
    assert asset["attestation"]
