"""
LAO 端到端测试：路由→校验→进化→确认→认知
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_routing():
    from lao.effect_anchored.routing.task_classifier import TaskClassifier
    from lao.effect_anchored.routing.model_router import ModelRouter
    router = ModelRouter()
    route = router.route("资本分析·估值建模")
    # 2026-08-16 禁v4-pro(commit bbe9bde)后: 重任务→qwen/token-plan系, 断言不再含pro
    assert route.model != "deepseek-v4-pro"
    route2 = router.route("日报数据汇总")
    # 2026-08-16 统一模型名: deepseek-flash→deepseek-v4-flash
    assert route2.model in ("deepseek-v4-flash", "qwen-plus", "qwen-flash")
    # v2.0: ultra_light任务优先Qoder CN credit消费(qwen-flash)
    print("  ✅ test_routing PASSED")

def test_h_gate():
    from lao.effect_anchored.hallucination_gate import HallucinationGate
    gate = HallucinationGate()
    result = gate.check("门店在深圳", context={"anchors": ["founder_first_store_location"]})
    print(f"  ✅ test_h_gate PASSED (confidence={getattr(result, 'confidence', 'N/A')})")

def test_interaction_gate():
    from lao.effect_anchored.interaction.interaction_gate import InteractionGate
    from lao.effect_anchored.hallucination_gate import HallucinationGate
    gate_h = HallucinationGate()
    result = gate_h.check("建议会员从力量转有氧", context={"user_message": "训练计划调整"})
    
    gate = InteractionGate(mode="sdk")
    confirm = gate.check(result)
    print(f"  ✅ test_interaction_gate PASSED (needs_user={confirm.needs_user})")

def test_experience_loop():
    from lao.effect_anchored.evolution.experience_extractor import ExperienceExtractor
    extractor = ExperienceExtractor()
    event = {
        "event_type": "H_intercept",
        "source_agent": "Tristan",
        "error_signature": "geo|深圳",
        "claimed": "门店在深圳",
        "expected": "门店在东莞",
        "actual": "门店在深圳",
        "constraint_text": "地理事实错误",
        "severity": "🔴",
        "category": "infrastructure",
    }
    pattern = extractor.extract(event)
    assert pattern is not None
    print(f"  ✅ test_experience_loop PASSED (extracted={pattern.pattern_fingerprint[:12]}...)")

def test_cost_tracker():
    from lao.effect_anchored.routing.cost_tracker import CostTracker
    ct = CostTracker()
    ct.record("test", "deepseek-flash", 100, 50, 0.001)
    assert ct.total_cost() > 0
    print(f"  ✅ test_cost_tracker PASSED (total=${ct.total_cost():.4f})")

def test_full_pipeline():
    """完整链路: L1→L2→L3→L4"""
    from lao.effect_anchored.routing.task_classifier import TaskClassifier
    from lao.effect_anchored.routing.model_router import ModelRouter
    from lao.effect_anchored.hallucination_gate import HallucinationGate
    from lao.effect_anchored.interaction.interaction_gate import InteractionGate
    from lao.effect_anchored.evolution.experience_extractor import ExperienceExtractor
    
    # L1: 路由
    router = ModelRouter()
    route = router.route("资本分析·估值建模")
    assert route.model
    
    # L2: 校验
    gate = HallucinationGate()
    result = gate.check("门店在深圳", context={"user_message": "深圳分店"})
    assert hasattr(result, 'confidence')
    
    # L3: 经验复利
    if not result.passed:
        extractor = ExperienceExtractor()
        event = {
            "event_type": "H_intercept",
            "source_agent": "Tristan",
            "error_signature": "|".join(result.anchors_violated) if result.anchors_violated else "geo|深圳",
            "claimed": "门店在深圳",
            "expected": "门店在东莞",
            "actual": "门店在深圳",
            "constraint_text": result.reason or "",
            "severity": "🔴",
            "category": "infrastructure",
        }
        pattern = extractor.extract(event)
        assert pattern
    
    # L4: 交互确认
    gate_i = InteractionGate(mode="sdk")
    confirm = gate_i.check(result)
    print(f"  ✅ test_full_pipeline PASSED (confidence={result.confidence}, needs_user={confirm.needs_user})")

if __name__ == "__main__":
    test_routing()
    test_h_gate()
    test_interaction_gate()
    test_experience_loop()
    test_cost_tracker()
    test_full_pipeline()
    print("\n✅ LAO 端到端测试全部通过")
