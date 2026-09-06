"""T2 统一任务身份字段 — 数据模型与 JSON Schema。

P1-A T2 规格落地（143-T2-Design）：
    - TaskIdentity 数据类：统一任务身份六字段 + 扩展元数据
    - JSON Schema 定义：用于序列化/反序列化校验
    - 工厂方法：从 OpenAI 兼容请求创建 TaskIdentity

约束：仅标准库 · 零外部依赖 · 全部合成数据验证
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

UTC = timezone.utc

# ── JSON Schema（与 P1-A T2 规格书 143-T2-Design 第四节对齐） ──────────

TASK_IDENTITY_SCHEMA: Dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "TaskIdentity",
    "description": "LAO 统一任务身份（P1-A T2 规格）",
    "type": "object",
    "required": ["task_id", "session_fp", "scope", "intent_locked"],
    "properties": {
        "task_id": {
            "type": "string",
            "format": "uuid",
            "description": "任务唯一标识（UUID v4）",
        },
        "parent_task_id": {
            "type": ["string", "null"],
            "format": "uuid",
            "description": "父任务 ID。主任务为 null；侧任务指向所属主任务。",
        },
        "session_fp": {
            "type": "string",
            "pattern": "^[a-f0-9]{64}$",
            "description": "会话指纹（SHA-256 全文 hex，64 位小写十六进制）。",
        },
        "scope": {
            "type": "string",
            "enum": ["main", "side"],
            "description": "任务作用域。main=主任务；side=侧任务。",
        },
        "intent_locked": {
            "type": "boolean",
            "description": "意图锁定标志。true=意图已锁定，剪枝不得删除。",
        },
        "correlation_id": {
            "type": "string",
            "description": "跨 provider 关联 ID。同一用户意图跨 provider 切换时不变。",
        },
        "agent": {
            "type": "string",
            "description": "Agent 名。空串表示未识别。",
        },
        "created_at": {
            "type": "string",
            "format": "date-time",
            "description": "任务创建时间（UTC ISO-8601）。",
        },
        "metadata": {
            "type": "object",
            "description": "扩展元数据。T2 阶段可为空对象。",
            "additionalProperties": True,
        },
    },
}

# ── 正则常量 ──────────────────────────────────────────────────────────

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_FP_RE = re.compile(r"^[a-f0-9]{64}$")
_CORRELATION_RE = re.compile(r"^corr-\d{8}-[0-9a-f]{8}$")


# ── 数据类 ────────────────────────────────────────────────────────────

@dataclass
class TaskIdentity:
    """LAO 统一任务身份。

    字段定义与 P1-A T2 规格书（143-T2-Design 第四节）完全对齐。
    """

    task_id: str
    session_fp: str
    scope: str = "main"
    intent_locked: bool = False
    parent_task_id: Optional[str] = None
    correlation_id: str = ""
    agent: str = ""
    created_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    # ── 工厂方法 ──────────────────────────────────────────────────

    @classmethod
    def create(
        cls,
        session_fp: str,
        parent_task_id: Optional[str] = None,
        agent: str = "",
        intent_locked: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "TaskIdentity":
        """创建新 TaskIdentity。

        Args:
            session_fp: 会话指纹（SHA-256 全文 hex，64 位）。
            parent_task_id: 父任务 ID（None=主任务）。
            agent: Agent 名。
            intent_locked: 意图锁定标志。
            metadata: 扩展元数据。

        Returns:
            新建的 TaskIdentity 实例。

        Raises:
            ValueError: session_fp 格式非法或 parent_task_id 格式非法。
        """
        if not _FP_RE.match(session_fp):
            raise ValueError(
                f"session_fp 必须为 64 位小写 hex（SHA-256），实际: {session_fp!r}"
            )
        if parent_task_id is not None and not _UUID_RE.match(parent_task_id):
            raise ValueError(
                f"parent_task_id 必须为合法 UUID v4，实际: {parent_task_id!r}"
            )

        task_id = str(uuid.uuid4())
        scope = "side" if parent_task_id is not None else "main"
        now = datetime.now(UTC).isoformat()
        corr = f"corr-{now[:10].replace('-', '')}-{task_id[:8]}"

        return cls(
            task_id=task_id,
            session_fp=session_fp,
            scope=scope,
            intent_locked=intent_locked,
            parent_task_id=parent_task_id,
            correlation_id=corr,
            agent=agent,
            created_at=now,
            metadata=metadata if metadata is not None else {},
        )

    # ── 校验 ──────────────────────────────────────────────────────

    def validate(self) -> List[str]:
        """校验所有字段格式，返回错误列表（空列表=通过）。"""
        errors: List[str] = []
        if not _UUID_RE.match(self.task_id):
            errors.append(f"task_id 非合法 UUID v4: {self.task_id!r}")
        if not _FP_RE.match(self.session_fp):
            errors.append(f"session_fp 非 64 位 hex: {self.session_fp!r}")
        if self.scope not in ("main", "side"):
            errors.append(f"scope 必须为 main/side: {self.scope!r}")
        if not isinstance(self.intent_locked, bool):
            errors.append(f"intent_locked 必须为 bool: {self.intent_locked!r}")
        if self.parent_task_id is not None and not _UUID_RE.match(self.parent_task_id):
            errors.append(f"parent_task_id 非合法 UUID v4: {self.parent_task_id!r}")
        if self.scope == "main" and self.parent_task_id is not None:
            errors.append("main 任务不应有 parent_task_id")
        if self.scope == "side" and self.parent_task_id is None:
            errors.append("side 任务必须有 parent_task_id")
        if self.correlation_id and not _CORRELATION_RE.match(self.correlation_id):
            errors.append(f"correlation_id 格式非法: {self.correlation_id!r}")
        return errors

    # ── 序列化 ────────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典（与 JSON Schema 对齐）。"""
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        """序列化为 JSON 字符串。"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskIdentity":
        """从字典反序列化。"""
        return cls(
            task_id=data["task_id"],
            session_fp=data["session_fp"],
            scope=data.get("scope", "main"),
            intent_locked=data.get("intent_locked", False),
            parent_task_id=data.get("parent_task_id"),
            correlation_id=data.get("correlation_id", ""),
            agent=data.get("agent", ""),
            created_at=data.get("created_at", ""),
            metadata=data.get("metadata", {}),
        )
