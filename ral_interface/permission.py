"""T8 权限边界定义。

P1-A T8 规格落地（151-T8-Design）：
    - 双闸权限模型：闸一 scope 标签 + 闸二 件④ ACL
    - 宪法级约束：ratify/withdraw 须创始人批准

约束：仅标准库 · 零外部依赖
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Set


class ScopeTag(str, Enum):
    """Scope 标签（闸一）。"""

    PERSONAL = "personal"           # 个人库：仅 subject_id 本人的卡
    COLLABORATIVE = "collaborative" # 协同库：该库全部已确权卡


class PermissionLevel(str, Enum):
    """权限级别。"""

    NONE = "none"           # 无权限
    READ = "read"           # 只读（retrieve_memory）
    WRITE = "write"         # 读写（+ register_memory）
    RATIFY = "ratify"       # 确权（+ ratify，须创始人批准）
    WITHDRAW = "withdraw"   # 撤回（+ withdraw，须创始人批准）


# 权限级别层次（高级包含低级）
PERMISSION_HIERARCHY: Dict[PermissionLevel, List[PermissionLevel]] = {
    PermissionLevel.NONE: [],
    PermissionLevel.READ: [PermissionLevel.READ],
    PermissionLevel.WRITE: [PermissionLevel.READ, PermissionLevel.WRITE],
    PermissionLevel.RATIFY: [PermissionLevel.READ, PermissionLevel.WRITE, PermissionLevel.RATIFY],
    PermissionLevel.WITHDRAW: [PermissionLevel.READ, PermissionLevel.WRITE,
                               PermissionLevel.RATIFY, PermissionLevel.WITHDRAW],
}


@dataclass(frozen=True)
class PermissionBoundary:
    """权限边界定义（不可变）。

    双闸模型：
    - 闸一：scope 标签（metadata.libraries_in_scope）
    - 闸二：件④ ACL（Locator.locate）

    宪法级约束：
    - ratify/withdraw 须创始人批准（不可绕过）
    """

    # 闸一：允许的 scope 标签
    allowed_scopes: Set[str] = frozenset({ScopeTag.PERSONAL.value, ScopeTag.COLLABORATIVE.value})

    # 闸二：ACL 判定点唯一（Locator.locate）
    acl_enforcement_point: str = "Locator.locate"

    # 宪法级约束
    ratify_requires_founder_approval: bool = True
    withdraw_requires_founder_approval: bool = True

    def validate_scope(self, scope: str) -> bool:
        """校验 scope 是否在允许范围内。"""
        return scope in self.allowed_scopes

    def requires_founder_approval(self, operation: str) -> bool:
        """查询操作是否须创始人批准。"""
        if operation == "ratify":
            return self.ratify_requires_founder_approval
        if operation == "withdraw":
            return self.withdraw_requires_founder_approval
        return False

    def check_permission(self, level: PermissionLevel, required: PermissionLevel) -> bool:
        """校验权限级别是否满足。"""
        granted = PERMISSION_HIERARCHY.get(level, [])
        return required in granted


# 默认权限边界实例
DEFAULT_BOUNDARY = PermissionBoundary()
