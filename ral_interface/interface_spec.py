"""T8 RAL 最小接口规格定义。

P1-A T8 规格落地（151-T8-Design）：
    - 4 个接口的函数签名与参数规范
    - 与 ral/memory/api.py 第 104-329 行实现对齐

约束：仅标准库 · 零外部依赖 · 不修改生产代码
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class MemoryState(str, Enum):
    """记忆卡状态枚举（与 ral/memory/state.py 对齐）。"""

    CANDIDATE = "candidate"
    RATIFIED = "ratified"
    WITHDRAWN = "withdrawn"


# 合法状态转换
ALLOWED_STATE_TRANSITIONS: Dict[MemoryState, List[MemoryState]] = {
    MemoryState.CANDIDATE: [MemoryState.RATIFIED, MemoryState.WITHDRAWN],
    MemoryState.RATIFIED: [MemoryState.WITHDRAWN],
    MemoryState.WITHDRAWN: [],  # 终态
}


@dataclass
class InterfaceSpec:
    """单个接口的规格定义。"""

    name: str
    params: List[str]
    returns: str
    constraints: List[str]
    audit_required: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "params": self.params,
            "returns": self.returns,
            "constraints": self.constraints,
            "audit_required": self.audit_required,
        }


# ── 4 接口规格定义（与 ral/memory/api.py 对齐） ──────────────────────

RETRIEVE_MEMORY_SPEC = InterfaceSpec(
    name="retrieve_memory",
    params=["subject_id: str", "scope: str", "query: str", "budget: int"],
    returns="Dict: {subject_id, libraries_in_scope, query, hits, denied_by_acl, ...}",
    constraints=[
        "query 为字面子串匹配，不语义检索",
        "budget 只截断片段，不截断引用",
        "只返回 ratified 卡",
        "每次调用经件④ 审计账",
    ],
    audit_required=True,
)

REGISTER_MEMORY_SPEC = InterfaceSpec(
    name="register_memory",
    params=["card_metadata: Dict", "readers: Optional[List[str]]"],
    returns="Dict: {versioned_card_id, artifact_vid, state, card}",
    constraints=[
        "两段式：件④ 先、件② 后",
        "subject_id 须先在件① 登记",
        "不凭记忆造主体",
        "残留不自行撤回（撤回须创始人批准）",
    ],
    audit_required=True,
)

RATIFY_SPEC = InterfaceSpec(
    name="ratify",
    params=["card_id: str", "approval: str", "version: Optional[int]", "actor: Optional[str]"],
    returns="Dict: {versioned_card_id, from_state, to_state, seq}",
    constraints=[
        "只校验签批凭据齐备性，不代行批准",
        "approval 须来自创始人或治理流程外部签批件（宪法级约束）",
        "不触及件④ 任何表，本层显式补落审计账",
    ],
    audit_required=True,
)

WITHDRAW_SPEC = InterfaceSpec(
    name="withdraw",
    params=["card_id: str", "founder_approval: str", "version: Optional[int]"],
    returns="Dict: {versioned_card_id, state, memory, locator}",
    constraints=[
        "须创始人批准（74号rev / 89号方案明载）",
        "两层同撤：件② 记忆卡与件④ 索引登记",
        "件④ withdraw 自带审计与凭据校验",
    ],
    audit_required=True,
)

# 4 接口注册表
RAL_MINIMAL_INTERFACES: Dict[str, InterfaceSpec] = {
    "retrieve_memory": RETRIEVE_MEMORY_SPEC,
    "register_memory": REGISTER_MEMORY_SPEC,
    "ratify": RATIFY_SPEC,
    "withdraw": WITHDRAW_SPEC,
}


def get_interface_spec(name: str) -> Optional[InterfaceSpec]:
    """获取接口规格。"""
    return RAL_MINIMAL_INTERFACES.get(name)


def validate_interface_name(name: str) -> bool:
    """校验接口名是否合法。"""
    return name in RAL_MINIMAL_INTERFACES
