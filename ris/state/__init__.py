"""
ris/state — Runtime State Registry 状态台账模块包。
Agent 运行状态台账(Phase 2 · Momo 负责)：谁健康 / 谁异常 / 谁正在恢复。
"""
from ris.state.runtime_state_registry import (
    RuntimeStateRegistry,
    RuntimeStateRecord,
    FailureRecord,
    MAX_FAILURE_HISTORY,
)

__all__ = [
    "RuntimeStateRegistry",
    "RuntimeStateRecord",
    "FailureRecord",
    "MAX_FAILURE_HISTORY",
]
