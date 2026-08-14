"""B 阶段测试: 按 Agent 绑定 Provider + Provider 内自动选 model(创始人 20:18 令)。

baron/ethan/momo → token-plan(qwen3.7-plus/glm-5.2/deepseek-v4-pro)
其他 → deepseek(flash/pro)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.routing.model_router import (
    ModelRouter, AGENT_PROVIDER_BINDING,
)


def test_agent_provider_binding_defined():
    """baron/ethan/momo 绑定 token-plan。"""
    assert AGENT_PROVIDER_BINDING == {"baron": "token-plan", "ethan": "token-plan", "momo": "token-plan"}


def test_baron_routes_in_token_plan():
    """baron → 只在 token-plan 池选(provider=token-plan)。"""
    r = ModelRouter()
    for tier in ("ultra_light", "light", "medium", "code"):
        sel = r.route_with_budget(tier, budget=5.0, agent="baron")
        assert sel.provider == "token-plan", f"baron/{tier} → {sel.provider}"


def test_baron_light_uses_qwen():
    """baron 轻任务 → qwen3.7-plus(便宜·有补贴)。"""
    r = ModelRouter()
    sel = r.route_with_budget("light", budget=5.0, agent="baron")
    assert sel.model == "qwen3.7-plus"
    assert sel.provider == "token-plan"


def test_shuyu_routes_in_deepseek():
    """shuyu → 只在 deepseek 池选。"""
    r = ModelRouter()
    for tier in ("light", "medium"):
        sel = r.route_with_budget(tier, budget=5.0, agent="shuyu")
        assert sel.provider == "deepseek", f"shuyu/{tier} → {sel.provider}"


def test_shuyu_light_uses_flash():
    """shuyu 轻任务 → deepseek-v4-flash。"""
    r = ModelRouter()
    sel = r.route_with_budget("light", budget=5.0, agent="shuyu")
    assert sel.model == "deepseek-v4-flash"


def test_token_plan_pool_has_qwen_glm():
    """token-plan 池含 qwen3.7-plus/glm-5.2/deepseek-v4-pro。"""
    r = ModelRouter()
    tp = {x["model"] for x in r.MODEL_POOL["medium"] if x.get("provider") == "token-plan"}
    assert "qwen3.7-plus" in tp
    assert "glm-5.2" in tp
    assert "deepseek-v4-pro" in tp


def test_unknown_agent_defaults_deepseek():
    """未绑定 agent → 默认 deepseek。"""
    r = ModelRouter()
    sel = r.route_with_budget("light", budget=5.0, agent="unknown-agent")
    assert sel.provider == "deepseek"
