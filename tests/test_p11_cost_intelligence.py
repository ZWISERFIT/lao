"""Phase2 P1-1 测试: Cost Intelligence Engine (LAO Impact Report·第一价值)。

创始人 v3.4 P1-1: 用户第一价值=Cost Saving。同一Agent 不开LAO成本X, 开LAO成本<X。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.routing.cost_intelligence import SavingsEngine, MODEL_BASELINE_COST


def test_saving_when_routed_to_cheaper_model():
    """同一请求路由到更便宜模型 → 产生节省。"""
    eng = SavingsEngine()
    ev = eng.compute_saving("stella", "light", "deepseek-v4-pro", "deepseek-v4-flash",
                            in_tok=1000, out_tok=200, quality_score=96, switch_reason="cost_redline")
    assert ev.saving_amount > 0
    assert ev.saving_ratio > 0
    assert ev.selected_model == "deepseek-v4-flash"


def test_no_saving_same_model():
    """不降级(quality gate保pro) → 节省0。"""
    eng = SavingsEngine()
    ev = eng.compute_saving("stella", "heavy_code", "deepseek-v4-pro", "deepseek-v4-pro",
                            in_tok=1000, out_tok=200, quality_score=96, switch_reason="quality_gate")
    assert ev.saving_amount == 0
    assert ev.saving_ratio == 0


def test_impact_report():
    """Dashboard LAO Impact Report: saved>0 + efficiency>10。"""
    eng = SavingsEngine()
    for i in range(100):
        if i % 10 == 0:
            eng.compute_saving("stella", "heavy", "deepseek-v4-pro", "deepseek-v4-pro",
                               in_tok=3000, out_tok=800, quality_score=96)
        else:
            eng.compute_saving("stella", "light", "deepseek-v4-pro", "deepseek-v4-flash",
                               in_tok=1500, out_tok=300, quality_score=96)
    rep = eng.impact_report()
    assert rep["requests"] == 100
    assert rep["saved"] > 0
    assert rep["efficiency"] > 10
    assert 0 <= rep["quality"] <= 100


def test_cost_savings_event_is_trust_event():
    """CostSavingsEvent → TrustEvent(subtype=EconomicEvent)。"""
    eng = SavingsEngine()
    ev = eng.compute_saving("nova", "task", "deepseek-v4-pro", "deepseek-v4-flash",
                            in_tok=1000, out_tok=100, quality_score=95)
    te = ev.to_trust_event()
    assert te["event"] == "CostSavings"
    assert te["subtype"] == "EconomicEvent"
    assert te["saving_amount"] > 0
    assert te["evidence_hash"]


def test_model_baseline_cost_defined():
    """基准成本表已定义(original_cost 计算依据)。"""
    assert "deepseek-v4-pro" in MODEL_BASELINE_COST
    assert "deepseek-v4-flash" in MODEL_BASELINE_COST
