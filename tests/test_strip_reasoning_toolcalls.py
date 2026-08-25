"""Regression tests for _strip_reasoning_content / tool-call preservation.

Doctrine history (both incidents are real and both fixes are in place):

- 2026-08-15 (Ethan, 86 errors of which 83 were thinking/400): the old
  _strip_reasoning_content popped tool_calls unconditionally ->
    (a) orphan role='tool' messages -> 400 "Messages with role 'tool' must be a
        response to a preceding message with 'tool_calls'"
    (b) assistant messages that only had reasoning_content + tool_calls became
        empty -> 400 "Invalid assistant message: content or tool_calls must be set"
  Fix: only drop reasoning_content, never tool_calls; backfill an empty content
  placeholder when a message would otherwise end up empty.

- C19 (2026-08-23, ral-b 400 death-loop): DeepSeek v4 requires reasoning_content
  to be passed back along with history. Stripping it on the DeepSeek channel
  caused 400 "reasoning_content ... must be passed back". Fix: _safe_payload now
  strips reasoning_content ONLY on non-DeepSeek channels; on DeepSeek channels
  the field is normalized (thinking blocks -> reasoning_content) and preserved.

So the current contract is channel-dependent:
  * DeepSeek channel     -> reasoning_content PRESERVED
  * non-DeepSeek channel -> reasoning_content STRIPPED
  * either channel       -> tool_calls preserved, no orphan tool messages,
                            no empty assistant messages
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.routing.lao_router_server import (
    _safe_payload, _strip_reasoning_content,
)

DEEPSEEK_MODEL = "deepseek-v4-flash"
OTHER_MODEL = "qwen-plus"  # downgrade channel: no reasoning_content support


def _body(model, messages, extra=None):
    """Request body that carries `thinking`, which no model supports -> drop path."""
    body = {"model": model, "messages": messages, "thinking": "off"}
    if extra:
        body.update(extra)
    return body


def _toolcall_messages():
    return [
        {"role": "user", "content": "what is the weather"},
        {"role": "assistant", "content": "", "reasoning_content": "thinking...",
         "tool_calls": [{"id": "t1", "type": "function",
                         "function": {"name": "get_weather", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "sunny 25C"},
    ]


def test_tool_calls_preserved_on_deepseek_channel():
    """2026-08-15 fix: dropping `thinking` must never break the tool-call chain."""
    payload, events = _safe_payload(_body(DEEPSEEK_MODEL, _toolcall_messages()),
                                   DEEPSEEK_MODEL)
    assert any(e["type"] == "CapabilityFallbackEvent" for e in events)
    out = payload["messages"]
    asst = [m for m in out if m.get("role") == "assistant"][0]
    assert asst.get("tool_calls"), "tool_calls must survive (never pop tool_calls)"
    tool_msgs = [m for m in out if m.get("role") == "tool"]
    assert len(tool_msgs) == 1, "tool message must not be orphaned"
    assert tool_msgs[0]["tool_call_id"] == "t1"


def test_tool_calls_preserved_on_non_deepseek_channel():
    """Same 2026-08-15 fix must hold on the channel that DOES strip."""
    payload, _ = _safe_payload(_body(OTHER_MODEL, _toolcall_messages()), OTHER_MODEL)
    out = payload["messages"]
    asst = [m for m in out if m.get("role") == "assistant"][0]
    assert asst.get("tool_calls"), "tool_calls must survive the strip path too"
    assert "reasoning_content" not in asst
    tool_msgs = [m for m in out if m.get("role") == "tool"]
    assert len(tool_msgs) == 1 and tool_msgs[0]["tool_call_id"] == "t1"


def test_reasoning_content_preserved_on_deepseek_channel():
    """C19: DeepSeek v4 needs reasoning_content passed back, so do NOT strip it."""
    messages = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "answer-1", "reasoning_content": "r1"},
        {"role": "assistant", "content": "answer-2", "reasoning_content": "r2"},
    ]
    payload, _ = _safe_payload(_body(DEEPSEEK_MODEL, messages), DEEPSEEK_MODEL)
    asst = [m for m in payload["messages"] if m.get("role") == "assistant"]
    assert len(asst) == 2
    assert [m.get("reasoning_content") for m in asst] == ["r1", "r2"], \
        "C19: stripping this on the DeepSeek channel reproduces the ral-b 400 loop"
    assert all(m.get("content") for m in asst), \
        "assistant messages that have content must keep it"


def test_reasoning_content_stripped_on_non_deepseek_channel():
    """Downgrade channels reject the unknown field -> it must be removed."""
    messages = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "answer-1", "reasoning_content": "r1"},
        {"role": "assistant", "content": "answer-2", "reasoning_content": "r2"},
    ]
    payload, _ = _safe_payload(_body(OTHER_MODEL, messages), OTHER_MODEL)
    for m in payload["messages"]:
        if m.get("role") == "assistant":
            assert "reasoning_content" not in m
            assert m.get("content"), "assistant messages with content must keep it"


def test_empty_assistant_msg_gets_placeholder_after_strip():
    """2026-08-15 fix (b): after stripping, an empty assistant message must be
    backfilled with an empty-string content, else 400 'content or tool_calls
    must be set'. Only the stripping channel can produce that situation."""
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "reasoning_content": "pure thought"},
    ]
    payload, _ = _safe_payload(_body(OTHER_MODEL, messages), OTHER_MODEL)
    asst = [m for m in payload["messages"] if m.get("role") == "assistant"][0]
    assert "reasoning_content" not in asst
    assert "content" in asst and asst["content"] == ""


def test_strip_helper_never_drops_tool_calls():
    """Unit-level guard on the helper itself, independent of channel routing."""
    messages = [
        {"role": "assistant", "content": "", "reasoning_content": "x",
         "tool_calls": [{"id": "t9", "type": "function",
                         "function": {"name": "f", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "t9", "content": "ok"},
    ]
    _strip_reasoning_content(messages)
    assert "reasoning_content" not in messages[0]
    assert messages[0]["tool_calls"][0]["id"] == "t9"
    assert messages[0]["content"] == ""
    assert messages[1]["role"] == "tool"


def test_empty_content_list_normalized_to_empty_string():
    """Multimodal residue: content=[] or [{'type':'text','text':''}] must become
    "" on the stripping channel, otherwise DeepSeek/others 400 on empty message."""
    messages = [
        {"role": "assistant", "content": [], "reasoning_content": "x"},
        {"role": "assistant", "content": [{"type": "text", "text": ""}],
         "reasoning_content": "y"},
    ]
    _strip_reasoning_content(messages)
    for m in messages:
        assert "reasoning_content" not in m
        assert m["content"] == ""


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
