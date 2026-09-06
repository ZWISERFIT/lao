"""T3 上下文剪枝规则与意图保持机制模块。

P1-A T3 规格落地（149-T3-Design）：
    - ContextPruner: 上下文剪枝引擎
    - PruningConfig: 剪枝配置
    - IntentKeeper: 意图保持率计算
    - IntentItem: 意图条目

导出核心接口，供 router_r3.py 集成时 import。
"""

from .pruner import ContextPruner, PruningConfig, PROTECTED_ROLES, PROTECTED_PATTERNS
from .intent_keeper import IntentKeeper, IntentItem

__all__ = [
    "ContextPruner",
    "PruningConfig",
    "PROTECTED_ROLES",
    "PROTECTED_PATTERNS",
    "IntentKeeper",
    "IntentItem",
]
