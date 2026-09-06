"""T7 取消与 checkpoint 状态机与止损规则模块。

P1-A T7 规格落地（147-T7-Design）：
    - TaskState: 6 种状态枚举
    - TaskStateMachine: 状态转换引擎
    - CheckpointData: checkpoint 数据结构
    - StopLossConfig: 止损阈值配置
    - StopLossMonitor: 运行时止损监控

导出核心接口，供 router_r3.py 集成时 import。
"""

from .state_machine import (
    TaskState,
    TaskStateMachine,
    VALID_TRANSITIONS,
    TERMINAL_STATES,
)
from .checkpoint import CheckpointData
from .stop_loss import (
    StopLossConfig,
    StopLossMonitor,
    DEFAULT_ALERT_TOKENS,
    DEFAULT_HARD_TOKENS,
    DEFAULT_MAX_RETRIES,
    DEFAULT_RETRY_WINDOW_SEC,
)

__all__ = [
    "TaskState",
    "TaskStateMachine",
    "VALID_TRANSITIONS",
    "TERMINAL_STATES",
    "CheckpointData",
    "StopLossConfig",
    "StopLossMonitor",
    "DEFAULT_ALERT_TOKENS",
    "DEFAULT_HARD_TOKENS",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_RETRY_WINDOW_SEC",
]
