"""Stella 派单·第2项验证：_strip_reasoning_content 带 thinking+tool 端到端(螺丝根治)。

需求: 不得删 tool_calls·带 thinking+tool 场景必须正常(不 400)。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "lao", "effect_anchored", "routing"))

from lao.effect_anchored.routing.lao_router_server import _strip_reasoning_content


def test_tool_calls_preserved():
    """thinking+tool 场景: tool_calls 必须保留(不得删)。"""
    msgs = [
        {"role": "user", "content": "请调用工具查天气"},
        {"role": "assistant", "reasoning_content": "thinking...",
         "tool_calls": [{"id": "t1", "type": "function",
                         "function": {"name": "get_weather", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "晴"},
    ]
    _strip_reasoning_content(msgs)
    # tool_calls 保留(工具链不断)
    assert msgs[1].get("tool_calls"), "tool_calls 被删=坐标链断裂(400根因)"
    # reasoning_content 被删(thinking 残留会 400)
    assert "reasoning_content" not in msgs[1]
    # 后续 tool 消息有前置 tool_calls → 合法
    assert msgs[2].get("tool_call_id") == "t1"


def test_thinking_param_not_in_payload():
    """thinking 参数不进 payload(不触发 DeepSeek 400)。"""
    from lao.effect_anchored.routing.lao_router_server import _safe_payload
    body = {"model": "deepseek-v4-flash", "thinking": "off",
            "messages": [{"role": "user", "content": "hi"}]}
    payload, events = _safe_payload(body, "deepseek-v4-flash")
    assert "thinking" not in payload


def test_empty_assistant_gets_placeholder():
    """空 assistant 消息补占位(不 400 空消息)。"""
    msgs = [{"role": "assistant", "reasoning_content": "only thinking"}]
    _strip_reasoning_content(msgs)
    assert msgs[0].get("content") == ""


def test_has_content_preserved():
    """有 content 的 assistant 消息保留 content。"""
    msgs = [{"role": "assistant", "reasoning_content": "r", "content": "正常回答"}]
    _strip_reasoning_content(msgs)
    assert msgs[0]["content"] == "正常回答"
    assert "reasoning_content" not in msgs[0]
