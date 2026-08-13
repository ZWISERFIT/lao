"""Phase2 P0-2 测试: Context Lifecycle Management (ContextIntegrityProtocol 完成)。

创始人 v3.4 P0-2: 从 Context 监控 → Context Lifecycle Management。
Risk 三层(Open/Private) + 禁止第三套权重(调用 FounderCognitivePolicy)。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.context_integrity import (
    ContextLifecycleManager, ContextObservation, ContextRiskObservation,
    FounderCognitiveEvaluator, RISK_FACTORS,
)

NORMAL = dict(bootstrap_size=20000, memory_injection_size=10000, compaction_frequency=0.5,
              context_growth_rate=500, token_efficiency=0.9, cpu_pressure=0.2,
              latency_pressure=0.15)
HIGH = dict(bootstrap_size=180000, memory_injection_size=90000, compaction_frequency=8,
            context_growth_rate=4500, token_efficiency=0.4, cpu_pressure=0.95,
            latency_pressure=0.9)


def test_normal_context_low_risk():
    """正常会话 → 低风险 → observe。"""
    mg = ContextLifecycleManager(threshold=0.7)
    r = mg.evaluate_context("sess-normal", NORMAL)
    assert r["action"] == "observe"
    assert r["risk_score"] < 0.7


def test_high_risk_context_mitigate():
    """高危会话(compaction异常+大bootstrap+cpu爆) → 高风险 → mitigate。"""
    mg = ContextLifecycleManager(threshold=0.7)
    r = mg.evaluate_context("sess-high", HIGH)
    assert r["action"] == "mitigate"
    assert r["risk_score"] >= 0.7


def test_risk_observation_normalized():
    """ContextRisk 观测: 各因子归一化 0~1·不预加权(开源)。"""
    mg = ContextLifecycleManager()
    obs = mg.observe("s", **HIGH)
    ro = mg.risk(obs)
    assert all(0.0 <= v <= 1.0 for v in ro.factors.values())
    assert set(ro.factors.keys()) == set(RISK_FACTORS)


def test_uses_founder_cognitive_policy():
    """Risk 评估引用 FounderCognitivePolicy(统一认知源·禁止第三套权重)。"""
    ev = FounderCognitiveEvaluator()
    assert ev._cognitive is not None  # CognitiveSystem 被引用


def test_context_events_emitted():
    """生命周期产出 ContextEvent 系事件(ContextRiskEvent 可审计)。"""
    mg = ContextLifecycleManager()
    mg.evaluate_context("s", NORMAL)
    mg.evaluate_context("s2", HIGH)
    types = {e["type"] for e in mg.events()}
    assert "ContextObservation" in types
    assert "ContextRiskEvent" in types
