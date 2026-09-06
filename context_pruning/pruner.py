"""T3 上下文剪枝引擎 — 剪枝规则与保护机制。

P1-A T3 规格落地（149-T3-Design）：
    - ContextPruner: 上下文剪枝引擎
    - 稳定前缀永不剪枝（system/工具配对/权限）
    - 动态部分可压缩（保留最近 N 轮）
    - 保护规则不可违反（意图/工具/权限）

约束：仅标准库 · 零外部依赖
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


# ── 保护角色枚举 ────────────────────────────────────────────────────

PROTECTED_ROLES: frozenset = frozenset({"system"})
PROTECTED_PATTERNS: frozenset = frozenset({
    "权限", "禁令", "permission", "prohibit", "forbidden",
})


@dataclass
class PruningConfig:
    """剪枝配置。"""

    keep_recent_turns: int = 5       # 保留最近 N 轮对话
    max_summary_tokens: int = 200    # 摘要最大 token 估算（按字符/4）
    protect_keywords: Set[str] = field(default_factory=lambda: set(PROTECTED_PATTERNS))

    def validate(self) -> list:
        errors = []
        if self.keep_recent_turns < 1:
            errors.append("keep_recent_turns 必须 >= 1")
        if self.max_summary_tokens < 1:
            errors.append("max_summary_tokens 必须 >= 1")
        return errors


class ContextPruner:
    """上下文剪枝引擎。

    规则：
    1. system 消息永不剪枝（稳定前缀）
    2. 包含保护关键词的消息永不剪枝
    3. 最近 N 轮对话保留原文
    4. 更早的历史对话压缩为摘要

    合成验证说明：
        本类仅处理传入的 messages 列表，不涉及任何真实数据。
    """

    def __init__(self, config: Optional[PruningConfig] = None) -> None:
        self._config = config or PruningConfig()

    @property
    def config(self) -> PruningConfig:
        return self._config

    def is_protected(self, message: Dict) -> bool:
        """判断消息是否受保护（不可剪枝）。

        保护条件（满足任一即受保护）：
        1. role 为 system
        2. content 包含保护关键词
        3. 消息包含 tool_calls 或 tool_call_id（工具调用配对）

        Args:
            message: OpenAI 兼容的 messages 元素。

        Returns:
            True=受保护，False=可剪枝。
        """
        role = message.get("role", "")
        if role in PROTECTED_ROLES:
            return True

        # 工具调用配对保护（193号件修复：只保护 assistant 发起方，tool 响应方可裁剪）
        if "tool_calls" in message and role == "assistant":
            return True
        # tool 角色消息：不保护，可被裁剪或截断
        if role == "tool":
            return False

        # 保护关键词
        content = str(message.get("content", ""))
        for kw in self._config.protect_keywords:
            if kw.lower() in content.lower():
                return True

        return False

    def prune(
        self,
        messages: List[Dict],
        intent: str = "",
    ) -> Dict[str, Any]:
        """执行剪枝。

        Args:
            messages: OpenAI 兼容的 messages 数组。
            intent: 当前意图文本（用于保护）。

        Returns:
            剪枝结果字典：
            {
                "pruned_messages": List[Dict],  # 剪枝后的消息列表
                "summary": str,                  # 被压缩历史的摘要
                "original_count": int,           # 原始消息数
                "pruned_count": int,             # 剪枝后消息数
                "protected_count": int,          # 受保护消息数
                "compressed_count": int,         # 被压缩消息数
            }
        """
        if not messages:
            return {
                "pruned_messages": [],
                "summary": "",
                "original_count": 0,
                "pruned_count": 0,
                "protected_count": 0,
                "compressed_count": 0,
            }

        # 深拷贝，不修改原始数据
        msgs = copy.deepcopy(messages)

        # 分类：受保护 vs 可压缩
        protected: List[Dict] = []
        compressible: List[Dict] = []

        for m in msgs:
            if self.is_protected(m):
                protected.append(m)
            else:
                compressible.append(m)

        # 193号件步③：tool 消息内容截断（保留头尾，砍中间）
        _TOOL_CONTENT_LIMIT = 4000
        for m in compressible:
            if m.get("role") == "tool":
                content = str(m.get("content", ""))
                if len(content) > _TOOL_CONTENT_LIMIT:
                    head = content[:_TOOL_CONTENT_LIMIT // 2]
                    tail = content[-_TOOL_CONTENT_LIMIT // 2:]
                    m["content"] = head + "\n...[truncated]...\n" + tail

        # 保留最近 N 轮（一轮 = 一条 user + 一条 assistant）
        keep_count = self._config.keep_recent_turns * 2
        if len(compressible) <= keep_count:
            # 无需压缩
            pruned = protected + compressible
            summary = ""
            compressed_count = 0
        else:
            # 压缩更早的历史
            to_compress = compressible[:-keep_count]
            to_keep = compressible[-keep_count:]
            summary = self._summarize(to_compress)
            pruned = protected + to_keep
            compressed_count = len(to_compress)

        # 保护当前意图：确保意图文本在剪枝后仍存在
        if intent:
            pruned = self._ensure_intent(pruned, intent)

        return {
            "pruned_messages": pruned,
            "summary": summary,
            "original_count": len(messages),
            "pruned_count": len(pruned),
            "protected_count": len(protected),
            "compressed_count": compressed_count,
        }

    def _summarize(self, messages: List[Dict]) -> str:
        """将被压缩消息生成摘要（P1-A 阶段为简单拼接，后续可接入 LLM）。

        Args:
            messages: 待压缩的消息列表。

        Returns:
            摘要字符串。
        """
        parts = []
        for m in messages:
            role = m.get("role", "unknown")
            content = str(m.get("content", ""))[:100]  # 截断
            parts.append(f"[{role}]: {content}")
        summary = " | ".join(parts)
        # 限制摘要长度
        max_chars = self._config.max_summary_tokens * 4
        if len(summary) > max_chars:
            summary = summary[:max_chars] + "..."
        return summary

    def _ensure_intent(
        self, messages: List[Dict], intent: str
    ) -> List[Dict]:
        """确保剪枝后意图仍存在。

        如果意图文本不在任何消息中，则追加一条 user 消息。

        Args:
            messages: 剪枝后的消息列表。
            intent: 当前意图文本。

        Returns:
            确保意图存在的消息列表。
        """
        if not intent:
            return messages

        # 检查意图是否已存在
        for m in messages:
            if intent in str(m.get("content", "")):
                return messages

        # 意图不存在 → 追加
        messages.append({"role": "user", "content": intent})
        return messages
