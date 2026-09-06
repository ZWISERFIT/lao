"""T8 RAL 最小接口与 Skill 契约模块。

P1-A T8 规格落地（151-T8-Design）：
    - 4 接口规格定义（与 ral/memory/api.py 对齐）
    - Skill 契约定义
    - 权限边界定义（双闸模型）

导出核心接口，供集成时 import。
"""

from .interface_spec import (
    MemoryState,
    InterfaceSpec,
    ALLOWED_STATE_TRANSITIONS,
    RAL_MINIMAL_INTERFACES,
    RETRIEVE_MEMORY_SPEC,
    REGISTER_MEMORY_SPEC,
    RATIFY_SPEC,
    WITHDRAW_SPEC,
    get_interface_spec,
    validate_interface_name,
)
from .skill_contract import (
    Operation,
    SkillContract,
    READ_ONLY_SKILL,
    WRITE_SKILL,
    ADMIN_SKILL,
    SKILL_REGISTRY,
)
from .permission import (
    ScopeTag,
    PermissionLevel,
    PermissionBoundary,
    PERMISSION_HIERARCHY,
    DEFAULT_BOUNDARY,
)

__all__ = [
    # interface_spec
    "MemoryState",
    "InterfaceSpec",
    "ALLOWED_STATE_TRANSITIONS",
    "RAL_MINIMAL_INTERFACES",
    "RETRIEVE_MEMORY_SPEC",
    "REGISTER_MEMORY_SPEC",
    "RATIFY_SPEC",
    "WITHDRAW_SPEC",
    "get_interface_spec",
    "validate_interface_name",
    # skill_contract
    "Operation",
    "SkillContract",
    "READ_ONLY_SKILL",
    "WRITE_SKILL",
    "ADMIN_SKILL",
    "SKILL_REGISTRY",
    # permission
    "ScopeTag",
    "PermissionLevel",
    "PermissionBoundary",
    "PERMISSION_HIERARCHY",
    "DEFAULT_BOUNDARY",
]
