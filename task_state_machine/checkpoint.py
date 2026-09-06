"""T7 Checkpoint 数据 — 保存/恢复任务状态。

P1-A T7 规格落地（147-T7-Design）：
    - CheckpointData: checkpoint 数据结构
    - 序列化/反序列化（JSON）
    - 与 TaskStateMachine 配合使用

约束：仅标准库 · 零外部依赖
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional

UTC = timezone.utc


@dataclass
class CheckpointData:
    """任务 checkpoint 数据。

    保存任务在 CHECKPOINT 状态时的完整上下文，
    用于恢复到 RUNNING 状态时还原执行环境。

    合成验证说明：
        本类仅管理 checkpoint 数据结构，不涉及任何真实数据。
    """

    task_id: str
    state: str = "CHECKPOINT"
    intent: str = ""
    intent_locked: bool = False
    tokens_consumed: int = 0
    messages_snapshot: list = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    @classmethod
    def create(
        cls,
        task_id: str,
        intent: str = "",
        intent_locked: bool = False,
        tokens_consumed: int = 0,
        messages_snapshot: Optional[list] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "CheckpointData":
        """创建 checkpoint。

        Args:
            task_id: 任务 ID。
            intent: 当前意图文本。
            intent_locked: 意图是否锁定。
            tokens_consumed: 已消耗 token 数。
            messages_snapshot: 消息快照（合成数据）。
            metadata: 扩展元数据。

        Returns:
            新建的 CheckpointData 实例。
        """
        return cls(
            task_id=task_id,
            state="CHECKPOINT",
            intent=intent,
            intent_locked=intent_locked,
            tokens_consumed=max(0, tokens_consumed),
            messages_snapshot=messages_snapshot if messages_snapshot is not None else [],
            metadata=metadata if metadata is not None else {},
            created_at=datetime.now(UTC).isoformat(),
        )

    def validate(self) -> list:
        """校验字段，返回错误列表。"""
        errors = []
        if not self.task_id:
            errors.append("task_id 不得为空")
        if self.state != "CHECKPOINT":
            errors.append(f"checkpoint.state 必须为 CHECKPOINT: {self.state!r}")
        if self.tokens_consumed < 0:
            errors.append("tokens_consumed 不得为负")
        return errors

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CheckpointData":
        return cls(
            task_id=data["task_id"],
            state=data.get("state", "CHECKPOINT"),
            intent=data.get("intent", ""),
            intent_locked=data.get("intent_locked", False),
            tokens_consumed=data.get("tokens_consumed", 0),
            messages_snapshot=data.get("messages_snapshot", []),
            metadata=data.get("metadata", {}),
            created_at=data.get("created_at", ""),
        )
