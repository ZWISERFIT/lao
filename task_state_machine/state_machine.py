"""T7 任务状态机 — 状态枚举、转换规则、TaskStateMachine。

P1-A T7 规格落地（147-T7-Design）：
    - TaskState: 6 种状态枚举
    - VALID_TRANSITIONS: 8 种合法转换
    - TaskStateMachine: 状态转换引擎（非法转换抛 ValueError）

约束：仅标准库 · 零外部依赖
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple


class TaskState(str, Enum):
    """任务状态枚举（P1-A T7 规格）。"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    CHECKPOINT = "CHECKPOINT"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# 合法状态转换表（8 种）
VALID_TRANSITIONS: Dict[TaskState, Set[TaskState]] = {
    TaskState.PENDING:    {TaskState.RUNNING},
    TaskState.RUNNING:    {TaskState.CHECKPOINT, TaskState.CANCELLED,
                           TaskState.COMPLETED, TaskState.FAILED},
    TaskState.CHECKPOINT: {TaskState.RUNNING, TaskState.CANCELLED,
                           TaskState.FAILED},
    TaskState.CANCELLED:  set(),   # 终态，不可转换
    TaskState.COMPLETED:  set(),   # 终态
    TaskState.FAILED:     set(),   # 终态
}

# 终态集合
TERMINAL_STATES: frozenset = frozenset({
    TaskState.CANCELLED, TaskState.COMPLETED, TaskState.FAILED,
})


class TaskStateMachine:
    """任务状态机。

    管理单个任务的状态转换，校验合法性，记录转换历史。

    合成验证说明：
        本类仅管理状态逻辑，不涉及任何真实数据或运行时。
    """

    def __init__(self, task_id: str, initial_state: TaskState = TaskState.PENDING) -> None:
        """初始化状态机。

        Args:
            task_id: 任务唯一标识。
            initial_state: 初始状态（默认 PENDING）。
        """
        self._task_id = task_id
        self._state = initial_state
        self._history: List[Tuple[str, TaskState, TaskState]] = []
        # intent_locked: 与 T2 TaskIdentity.intent_locked 对齐
        self._intent_locked: bool = False

    @property
    def task_id(self) -> str:
        return self._task_id

    @property
    def state(self) -> TaskState:
        return self._state

    @property
    def is_terminal(self) -> bool:
        """是否处于终态。"""
        return self._state in TERMINAL_STATES

    @property
    def intent_locked(self) -> bool:
        return self._intent_locked

    @intent_locked.setter
    def intent_locked(self, value: bool) -> None:
        self._intent_locked = bool(value)

    @property
    def history(self) -> List[Tuple[str, TaskState, TaskState]]:
        """转换历史：[(timestamp, from_state, to_state), ...]。"""
        return list(self._history)

    def can_transition(self, target: TaskState) -> bool:
        """查询是否可以转换到目标状态。"""
        return target in VALID_TRANSITIONS.get(self._state, set())

    def transition(self, target: TaskState, timestamp: str = "") -> None:
        """执行状态转换。

        Args:
            target: 目标状态。
            timestamp: 转换时间戳（可选，用于审计）。

        Raises:
            ValueError: 转换不合法。
        """
        if not self.can_transition(target):
            raise ValueError(
                f"非法状态转换: {self._state.value} → {target.value} "
                f"(task_id={self._task_id})"
            )
        old = self._state
        self._state = target
        self._history.append((timestamp or "", old, target))

    # ── 便捷方法 ──────────────────────────────────────────────────

    def start(self, timestamp: str = "") -> None:
        """PENDING → RUNNING。"""
        self.transition(TaskState.RUNNING, timestamp)

    def save_checkpoint(self, timestamp: str = "") -> None:
        """RUNNING → CHECKPOINT。"""
        self.transition(TaskState.CHECKPOINT, timestamp)

    def resume(self, timestamp: str = "") -> None:
        """CHECKPOINT → RUNNING。"""
        self.transition(TaskState.RUNNING, timestamp)

    def cancel(self, timestamp: str = "") -> None:
        """→ CANCELLED（从 RUNNING 或 CHECKPOINT）。"""
        self.transition(TaskState.CANCELLED, timestamp)

    def complete(self, timestamp: str = "") -> None:
        """RUNNING → COMPLETED。"""
        self.transition(TaskState.COMPLETED, timestamp)

    def fail(self, timestamp: str = "") -> None:
        """→ FAILED（从 RUNNING 或 CHECKPOINT）。"""
        self.transition(TaskState.FAILED, timestamp)

    # ── 序列化 ────────────────────────────────────────────────────

    def to_dict(self) -> Dict:
        """序列化为字典。"""
        return {
            "task_id": self._task_id,
            "state": self._state.value,
            "is_terminal": self.is_terminal,
            "intent_locked": self._intent_locked,
            "history": [
                {"ts": ts, "from": f.value, "to": t.value}
                for ts, f, t in self._history
            ],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)
