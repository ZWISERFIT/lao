"""OpenClaw / OpenAI 兼容语境适配器（180号方案：直接裁剪 payload["tools"]）。

落点（S1 现场核定，见 `n1/s1b_report.txt`）：
    lao_router_server.py :: chat_completions()
        payload, cap_events = _safe_payload(body, chosen_model)   # ← 此行之后
        + N1 段：compress_payload(payload, _N1, user_text=..., agent=..., request_id=...)

为什么不按 180号 §4.1 字面（"_stabilize_messages 之后、_safe_payload 之前"）：
    真实代码里 `_stabilize_messages` 不是独立管线步骤，而是 `_safe_payload` 内部调用的
    一步；`tools` 也是在 `_safe_payload` 里按 SUPPORTED_PARAMS 过滤进 payload 的。因此
    「messages 已稳定化 且 tools 已就位」的唯一位置就是 `_safe_payload` 返回之后。语义与
    180号 §4.1 意图一致，落点按现场修正。

红线：
    C1/C3  只动 payload["tools"]，绝不碰 payload["messages"]（用户文本只读）
    C2     决策为 noop 时不写 payload（连 key 顺序都不动）
    C5     enabled 默认 False，一键回滚
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..engine import CognitiveCompressor, CompressionDecision
from ..metrics import record_decision

#: 指标里的语境标识。DSH 侧为 "dsh"，两者数据不可混算。
OPENCLAW_CONTEXT = "openclaw"
_ADAPTER = "openclaw.payload_tools"


def _tool_name(tool: Any) -> str:
    """从一条 tool schema 里取名字。

    兼容三种形态：
        {"type": "function", "function": {"name": ...}}   OpenAI 现行
        {"name": ...}                                     裸形态 / Anthropic 风
        其他                                              返回 ""（视为不可识别）
    """
    if not isinstance(tool, dict):
        return ""
    fn = tool.get("function")
    if isinstance(fn, dict) and fn.get("name"):
        return str(fn["name"])
    if tool.get("name"):
        return str(tool["name"])
    return ""


def extract_tool_names(tools: Any) -> List[str]:
    """按 tools 数组原顺序取出工具名；取不到名字的位置留空串占位。"""
    if not isinstance(tools, (list, tuple)):
        return []
    return [_tool_name(t) for t in tools]


def _text_of_content(content: Any) -> str:
    """把 message.content 归一成纯文本（只读，不回写）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for seg in content:
            if isinstance(seg, dict):
                if isinstance(seg.get("text"), str):
                    parts.append(seg["text"])
            elif isinstance(seg, str):
                parts.append(seg)
        return "\n".join(parts)
    return ""


def iter_user_text(messages: Any, max_messages: int = 2) -> str:
    """取最近 max_messages 条 user 消息文本用于意图判定。

    只取 user 角色：system 提示词里往往把所有工具能力都描述一遍，掺进去会让意图分类
    永远命中多个域，压缩率归零。

    Args:
        messages: payload["messages"]，只读。
        max_messages: 从尾部往前取几条 user 消息。

    Returns:
        拼接文本（越靠后的消息排在越后）。
    """
    if not isinstance(messages, (list, tuple)):
        return ""
    picked: List[str] = []
    for msg in reversed(messages):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        text = _text_of_content(msg.get("content"))
        if text.strip():
            picked.append(text)
        if len(picked) >= max_messages:
            break
    return "\n".join(reversed(picked))


def _chars(tools: Any) -> int:
    """tools 字段序列化后的字符数（口径同 180号 §3 基线测量）。"""
    try:
        return len(json.dumps(tools, ensure_ascii=False))
    except Exception:
        return 0


def compress_payload(
    payload: Dict[str, Any],
    compressor: CognitiveCompressor,
    *,
    user_text: Optional[str] = None,
    agent: str = "",
    request_id: str = "",
    metrics_path: Optional[str] = None,
) -> CompressionDecision:
    """就地裁剪 payload["tools"]。

    Args:
        payload: `_safe_payload` 的产物。仅当决策为真实裁剪时才写入其 "tools" 键。
        compressor: 引擎实例（进程内单例，缓存/黑名单随之常驻）。
        user_text: 显式给定的意图文本；None 时从 payload["messages"] 里取 user 消息。
        agent: 归因 agent（`_extract_agent` 的结果）。
        request_id: 请求 id，便于与路由事件日志对齐。
        metrics_path: 指标文件；None 时用 compressor.config.metrics_path。

    Returns:
        CompressionDecision。调用方通常只需看 `decision.is_noop` 与 `decision.dropped`；
        `decision.dropped` 需随请求上下文留存，供兜底 3 判定误裁。
    """
    tools = payload.get("tools") if isinstance(payload, dict) else None
    names = extract_tool_names(tools)
    text = user_text if user_text is not None else iter_user_text(payload.get("messages"))

    decision = compressor.decide(names, text)

    chars_before = _chars(tools)
    chars_after = chars_before
    if not decision.is_noop:
        keep = set(decision.kept)
        kept_tools = [t for t in tools if _tool_name(t) in keep]
        # 防御性不变量：按名字过滤出的条数必须与决策条数一致，否则宁可不动。
        # 按当前实现该分支不可达（域判定纯按名字，重名工具必然同留同弃，条数自洽），
        # 保留它是为了在 _tool_name 归一规则将来变化时立刻退回透传而不是错裁。
        if len(kept_tools) == decision.kept_count:
            payload["tools"] = kept_tools
            chars_after = _chars(kept_tools)
        else:
            decision = CompressionDecision(
                intent=decision.intent,
                kept=tuple(names),
                dropped=(),
                original_count=decision.original_count,
                fallback="name_collision",
                cache_hit=decision.cache_hit,
                latency_ms=decision.latency_ms,
            )

    path = compressor.config.metrics_path if metrics_path is None else metrics_path
    if path:
        record_decision(
            path,
            decision,
            context=OPENCLAW_CONTEXT,
            adapter=_ADAPTER,
            chars_before=chars_before,
            chars_after=chars_after,
            agent=agent,
            request_id=request_id,
        )
    return decision


def detect_misprune(response: Any, dropped: Sequence[str]) -> Tuple[bool, str]:
    """检查下游响应是否请求了被裁掉的工具（180号 §4.6 兜底 3 的检测信号）。

    Args:
        response: 下游返回的 dict（OpenAI chat.completion 形态）。
        dropped: 本次被裁掉的工具名。

    Returns:
        (是否误裁, 被误裁的工具名)。未命中时返回 (False, "")。
    """
    if not dropped or not isinstance(response, dict):
        return False, ""
    dropped_set = {str(d) for d in dropped}
    for choice in response.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            continue
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            fn = call.get("function")
            name = str(fn.get("name")) if isinstance(fn, dict) else str(call.get("name") or "")
            if name in dropped_set:
                return True, name
    return False, ""
