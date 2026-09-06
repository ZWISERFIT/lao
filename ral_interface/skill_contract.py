"""T8 Skill 契约定义。

P1-A T8 规格落地（151-T8-Design）：
    - SkillContract: Skill 契约数据结构
    - Skill 是 Agent 可调用的能力单元
    - 定义所需权限、允许操作、审计要求

约束：仅标准库 · 零外部依赖
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set


class Operation(str, Enum):
    """允许的操作类型。"""

    READ = "read"           # retrieve_memory
    WRITE = "write"         # register_memory
    RATIFY = "ratify"       # ratify（须创始人批准）
    WITHDRAW = "withdraw"   # withdraw（须创始人批准）


@dataclass
class SkillContract:
    """Skill 契约。

    定义一个 Skill 的能力边界：
    - 所需权限
    - 允许的操作
    - 是否需审计

    合成验证说明：
        本类仅管理 Skill 契约数据结构，不涉及任何真实数据。
    """

    skill_id: str
    name: str
    description: str = ""
    required_permissions: List[str] = field(default_factory=list)
    allowed_operations: Set[str] = field(default_factory=lambda: {Operation.READ.value})
    audit_required: bool = True
    requires_founder_approval: bool = False  # ratify/withdraw 须创始人批准

    def validate(self) -> List[str]:
        """校验契约合法性。"""
        errors = []
        if not self.skill_id:
            errors.append("skill_id 不得为空")
        if not self.name:
            errors.append("name 不得为空")
        # 校验操作合法性
        valid_ops = {op.value for op in Operation}
        for op in self.allowed_operations:
            if op not in valid_ops:
                errors.append(f"非法操作: {op!r}，合法值: {valid_ops}")
        # ratify/withdraw 须创始人批准
        if Operation.RATIFY.value in self.allowed_operations or Operation.WITHDRAW.value in self.allowed_operations:
            if not self.requires_founder_approval:
                errors.append("ratify/withdraw 操作须 requires_founder_approval=True")
        return errors

    def can_perform(self, operation: str) -> bool:
        """查询是否允许执行某操作。"""
        return operation in self.allowed_operations

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "description": self.description,
            "required_permissions": self.required_permissions,
            "allowed_operations": sorted(self.allowed_operations),
            "audit_required": self.audit_required,
            "requires_founder_approval": self.requires_founder_approval,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SkillContract":
        return cls(
            skill_id=data["skill_id"],
            name=data["name"],
            description=data.get("description", ""),
            required_permissions=data.get("required_permissions", []),
            allowed_operations=set(data.get("allowed_operations", [Operation.READ.value])),
            audit_required=data.get("audit_required", True),
            requires_founder_approval=data.get("requires_founder_approval", False),
        )


# ── 预定义 Skill 模板 ────────────────────────────────────────────────

READ_ONLY_SKILL = SkillContract(
    skill_id="skill:read_only",
    name="只读记忆检索",
    description="仅允许 retrieve_memory，不可写入/确权/撤回",
    required_permissions=["memory:read"],
    allowed_operations={Operation.READ.value},
    audit_required=True,
    requires_founder_approval=False,
)

WRITE_SKILL = SkillContract(
    skill_id="skill:write",
    name="记忆登记",
    description="允许 retrieve_memory + register_memory",
    required_permissions=["memory:read", "memory:write"],
    allowed_operations={Operation.READ.value, Operation.WRITE.value},
    audit_required=True,
    requires_founder_approval=False,
)

ADMIN_SKILL = SkillContract(
    skill_id="skill:admin",
    name="管理员（含确权/撤回）",
    description="全部 4 个接口，ratify/withdraw 须创始人批准",
    required_permissions=["memory:read", "memory:write", "memory:ratify", "memory:withdraw"],
    allowed_operations={op.value for op in Operation},
    audit_required=True,
    requires_founder_approval=True,
)

# Skill 注册表
SKILL_REGISTRY: Dict[str, SkillContract] = {
    "read_only": READ_ONLY_SKILL,
    "write": WRITE_SKILL,
    "admin": ADMIN_SKILL,
}
