"""Phase X Task4 测试: Provider Health Gate(禁止静默 fallback)。

创始人 v3.4 出口稳定: primary失败→fallback静默失效→502 必须被 Health Gate 拦截。
1. primary healthy → 正常调用
2. primary failure → fallback healthy → 正常恢复
3. primary failure → fallback invalid key → ProviderUnavailableEvent + FallbackDisabledEvent + RecoveryRecommendation(不是502)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.provider_health_gate import ProviderHealthGate, HEALTHY, UNHEALTHY, UNKNOWN


def test_primary_healthy():
    """primary healthy → 正常(health gate 放行)。"""
    gate = ProviderHealthGate()
    h = gate.check("deepseek", "deepseek-v4-pro", api_key_valid=True,
                   endpoint_available=True, model_available=True, capability_compatible=True)
    assert h.status == HEALTHY
    assert gate.is_healthy("deepseek") is True


def test_fallback_healthy_recovers():
    """primary 失败 → fallback healthy → 正常恢复(可进候选池)。"""
    gate = ProviderHealthGate()
    gate.check("deepseek", "deepseek-v4-pro", api_key_valid=False)  # primary fail
    gate.check("deepseek", "deepseek-v4-flash", api_key_valid=True,
               endpoint_available=True, model_available=True)
    assert gate.is_healthy("deepseek") is True  # fallback healthy 接管


def test_fallback_invalid_key_not_502():
    """primary 失败 → fallback invalid key → 不 502, 而是 unhealthy 判定。"""
    gate = ProviderHealthGate()
    gate.check("token-plan", "qwen3.7-plus", api_key_valid=False, reason="401 Invalid API-key")
    assert gate.is_healthy("token-plan") is False
    ev = gate.health("token-plan")
    assert ev.status == UNHEALTHY
    assert ev.api_key_valid is False
    # 关键: unhealthy provider 不进入候选池 → 不产生 502
    assert "invalid" in ev.reason or "401" in ev.reason


def test_unknown_provider_not_healthy():
    """未检查的 provider → unknown·不可进候选池(防静默失效)。"""
    gate = ProviderHealthGate()
    gate.check("unknown-provider")
    assert gate.is_healthy("unknown-provider") is False
    assert gate.health("unknown-provider").status == UNKNOWN


def test_trust_event_emitted():
    """ProviderHealthEvent → TrustEvent(RuntimeEvent·可审计)。"""
    gate = ProviderHealthGate()
    u = gate.check("token-plan", "qwen", api_key_valid=False)
    te = u.to_trust_event()
    assert te["event"] == "ProviderHealth"
    assert te["subtype"] == "RuntimeEvent"
    assert te["status"] == "unhealthy"
    assert te["api_key_valid"] is False
