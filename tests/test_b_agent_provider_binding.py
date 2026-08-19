"""B 阶段测试: 按 Agent 绑定 Provider + 配额感知 failover(2026-08-19 更新)。

背景(2026-08-19 双失联根因②): token-plan 周配额耗尽(429实证)·
创始人令·额度感知: token-plan 耗尽 → 自动 failover 到可用 provider。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.routing.model_router import (
    ModelRouter, AGENT_PROVIDER_BINDING,
)


def test_agent_provider_binding_defined():
    """baron/ethan/momo 绑定 token-plan(绑定声明保留)。"""
    assert AGENT_PROVIDER_BINDING == {"baron": "token-plan", "ethan": "token-plan", "momo": "token-plan"}


def test_token_plan_quota_exhausted():
    """token-plan 配额耗尽 → _check_provider_quota 返回 False(额度感知)。"""
    r = ModelRouter()
    assert r._check_provider_quota("token-plan") is False
    # 直连 provider 非配额制 → 默认可用
    assert r._check_provider_quota("qwen") is True
    assert r._check_provider_quota("deepseek") is True


def test_baron_failover_when_token_plan_dead():
    """token-plan 耗尽 → baron 自动 failover(不选 token-plan)。"""
    r = ModelRouter()
    for tier in ("ultra_light", "light", "medium", "code"):
        sel = r.route_with_budget(tier, budget=5.0, agent="baron")
        assert sel.provider != "token-plan", f"baron/{tier} 不应走耗尽的 token-plan"
        assert sel.provider in ("qwen", "deepseek"), f"baron/{tier} → {sel.provider}"


def test_shuyu_routes_in_deepseek():
    """shuyu → 只在 deepseek 池选(绑定未变)。"""
    r = ModelRouter()
    for tier in ("light", "medium"):
        sel = r.route_with_budget(tier, budget=5.0, agent="shuyu")
        assert sel.provider == "deepseek", f"shuyu/{tier} → {sel.provider}"


def test_shuyu_light_uses_flash():
    """shuyu 轻任务 → deepseek-v4-flash。"""
    r = ModelRouter()
    sel = r.route_with_budget("light", budget=5.0, agent="shuyu")
    assert sel.model == "deepseek-v4-flash"


def test_dynamic_pool_has_qwen_glm():
    """动态池含 qwen3.7-flash/qwen3.6-flash/glm-5.2(从 config 动态构建)。"""
    r = ModelRouter()
    all_models = {x["model"] for x in r.MODEL_POOL["medium"]}
    assert "qwen3.7-flash" in all_models   # qwen 直连优先
    assert "qwen3.6-flash" in all_models   # token-plan(创始人点名)
    assert "glm-5.2" in all_models
    assert "deepseek-v4-flash" in all_models


def test_dynamic_pool_qwen_first():
    """qwen 直连为每个 tier 首选(去硬编码后·不再 deepseek 写死首选)。"""
    r = ModelRouter()
    for tier in ("light", "medium", "code"):
        first = r.MODEL_POOL[tier][0]
        assert first["provider"] == "qwen", f"{tier} 首选 → {first}"


def test_unknown_agent_defaults_deepseek():
    """未绑定 agent → 默认 deepseek(绑定逻辑保留)。"""
    r = ModelRouter()
    sel = r.route_with_budget("light", budget=5.0, agent="unknown-agent")
    assert sel.provider == "deepseek"
