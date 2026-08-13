"""Phase2 P1-2 测试: Model Intelligence Matrix (智能路由·从价格到能力)。

创始人 v3.4 P1-2: lao-router 从价格路由升级为 Intelligence Routing。
决策链: Task→Capability Match→Cost Constraint→Quality Gate→Model Decision。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.routing.model_intelligence import (
    make_default_matrix, ProviderIntelligenceMatrix, ModelCapability,
)


def test_reasoning_task_picks_capable_model():
    """reasoning 任务 → 能力匹配优先(pro·reasoning 98)，非纯价格。"""
    mx = make_default_matrix()
    d = mx.route("reasoning", cost_budget=1.0, min_quality=85)
    assert d["chosen_model"] == "deepseek-v4-pro"
    assert d["task_score"] == 98


def test_light_task_cost_constraint_picks_flash():
    """低成本任务 + 低预算 → 成本约束选 flash。"""
    mx = make_default_matrix()
    d = mx.route("light", cost_budget=0.002, min_quality=80, latency_pref="low")
    assert d["chosen_model"] == "deepseek-v4-flash"


def test_quality_gate_filters():
    """质量门过滤: 无模型达标时回退到最优可用, 并如实标记 gate 未全过。"""
    mx = make_default_matrix()
    # min_quality=90: flash(88) 被滤, pro(96) 过 → pro 且 gate 通过
    d = mx.route("code", cost_budget=1.0, min_quality=90)
    assert d["quality_gate_passed"] is True
    assert d["chosen_model"] == "deepseek-v4-pro"
    # min_quality=99: 两者都不达 → 回退最优, gate 标记 False
    d2 = mx.route("code", cost_budget=1.0, min_quality=99)
    assert d2["quality_gate_passed"] is False
    assert d2["chosen_model"] in ("deepseek-v4-pro", "deepseek-v4-flash")


def test_decision_emits_trust_event():
    """路由决策 → TrustEvent(ModelDecision · EconomicEvent)。"""
    mx = make_default_matrix()
    d = mx.route("light", cost_budget=0.002, min_quality=80)
    te = mx.to_trust_event(d)
    assert te["event"] == "ModelDecision"
    assert te["subtype"] == "EconomicEvent"
    assert te["chosen_model"] == d["chosen_model"]


def test_register_and_get():
    """注册模型 → 可检索。"""
    mx = ProviderIntelligenceMatrix()
    mx.register(ModelCapability(provider="p", model="m1", task_fit={"t": 90}))
    assert mx.get("m1").model == "m1"
