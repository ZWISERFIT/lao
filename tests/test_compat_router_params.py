"""LAO v3.4 Compatibility Repair · 参数过滤/能力协商测试 (Phase D)。

根因: OpenAI Compatible 未知参数(thinking)透传 → Completions.create() TypeError。
修复: SUPPORTED_PARAMS 白名单 + ProviderCapabilityRegistry 能力协商。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "lao", "effect_anchored", "routing"))

from lao.effect_anchored.routing.lao_router_server import (
    _safe_payload, _capability, SUPPORTED_PARAMS,
)


def test_thinking_param_dropped():
    """thinking 参数不被支持 → 被过滤 + CapabilityFallbackEvent。"""
    body = {"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": "hi"}],
            "thinking": "off"}
    payload, events = _safe_payload(body, "deepseek-v4-flash")
    assert "thinking" not in payload          # 被 drop
    assert payload["model"] == "deepseek-v4-flash"
    assert any(e["type"] == "CapabilityFallbackEvent" for e in events)


def test_unknown_param_filtered():
    """未知参数 → 白名单过滤(不进入 create)。"""
    body = {"model": "deepseek-v4-flash", "messages": [], "unknown_param_xyz": 123}
    payload, _ = _safe_payload(body, "deepseek-v4-flash")
    assert "unknown_param_xyz" not in payload
    assert "messages" in payload              # 已知参数保留


def test_supported_params_retained():
    """支持的参数(stream/max_tokens/tools)保留。"""
    body = {"model": "deepseek-v4-flash", "messages": [], "stream": True,
            "max_tokens": 100, "tools": [{"type": "function"}]}
    payload, _ = _safe_payload(body, "deepseek-v4-flash")
    assert payload["stream"] is True
    assert payload["max_tokens"] == 100
    assert "tools" in payload


def test_capability_registry():
    """能力注册表: thinking=False·stream=True 等。"""
    cap = _capability("deepseek-v4-pro")
    assert cap["thinking"] is False
    assert cap["stream"] is True
    assert cap["reasoning_content"] is True


def test_supported_params_defined():
    """白名单定义了核心参数。"""
    for p in ("model", "messages", "stream", "temperature", "max_tokens", "tools"):
        assert p in SUPPORTED_PARAMS
