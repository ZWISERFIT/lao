"""T8 RAL 最小接口与 Skill 契约 — 单元测试（全部合成数据）。

P1-A T8 规格落地（151-T8-Design）：
    - 4 接口规格定义与校验
    - Skill 契约创建/校验/序列化
    - 权限边界与双闸模型

运行方式：
    python -m unittest ral_interface.test_ral_interface -v
"""

from __future__ import annotations

import json
import unittest

from ral_interface.interface_spec import (
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
from ral_interface.skill_contract import (
    Operation,
    SkillContract,
    READ_ONLY_SKILL,
    WRITE_SKILL,
    ADMIN_SKILL,
    SKILL_REGISTRY,
)
from ral_interface.permission import (
    ScopeTag,
    PermissionLevel,
    PermissionBoundary,
    PERMISSION_HIERARCHY,
    DEFAULT_BOUNDARY,
)


# ── 接口规格测试 ─────────────────────────────────────────────────────

class TestInterfaceSpec(unittest.TestCase):
    """4 接口规格定义。"""

    def test_four_interfaces_defined(self):
        self.assertEqual(len(RAL_MINIMAL_INTERFACES), 4)

    def test_retrieve_memory_spec(self):
        spec = RETRIEVE_MEMORY_SPEC
        self.assertEqual(spec.name, "retrieve_memory")
        self.assertEqual(len(spec.params), 4)
        self.assertTrue(spec.audit_required)

    def test_register_memory_spec(self):
        spec = REGISTER_MEMORY_SPEC
        self.assertEqual(spec.name, "register_memory")
        self.assertEqual(len(spec.params), 2)

    def test_ratify_spec(self):
        spec = RATIFY_SPEC
        self.assertEqual(spec.name, "ratify")
        self.assertTrue(any("宪法级" in c for c in spec.constraints))

    def test_withdraw_spec(self):
        spec = WITHDRAW_SPEC
        self.assertEqual(spec.name, "withdraw")
        self.assertTrue(any("创始人" in c for c in spec.constraints))

    def test_get_interface_spec(self):
        spec = get_interface_spec("retrieve_memory")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.name, "retrieve_memory")

    def test_get_unknown_interface(self):
        spec = get_interface_spec("unknown")
        self.assertIsNone(spec)

    def test_validate_interface_name(self):
        self.assertTrue(validate_interface_name("retrieve_memory"))
        self.assertTrue(validate_interface_name("ratify"))
        self.assertFalse(validate_interface_name("unknown"))

    def test_spec_serialization(self):
        d = RETRIEVE_MEMORY_SPEC.to_dict()
        self.assertEqual(d["name"], "retrieve_memory")
        self.assertIn("params", d)
        self.assertIn("constraints", d)


class TestMemoryState(unittest.TestCase):
    """记忆卡状态枚举。"""

    def test_three_states(self):
        self.assertEqual(len(MemoryState), 3)

    def test_allowed_transitions(self):
        # candidate → ratified / withdrawn
        self.assertIn(MemoryState.RATIFIED, ALLOWED_STATE_TRANSITIONS[MemoryState.CANDIDATE])
        self.assertIn(MemoryState.WITHDRAWN, ALLOWED_STATE_TRANSITIONS[MemoryState.CANDIDATE])
        # ratified → withdrawn
        self.assertIn(MemoryState.WITHDRAWN, ALLOWED_STATE_TRANSITIONS[MemoryState.RATIFIED])
        # withdrawn → (终态)
        self.assertEqual(ALLOWED_STATE_TRANSITIONS[MemoryState.WITHDRAWN], [])


# ── Skill 契约测试 ───────────────────────────────────────────────────

class TestSkillContract(unittest.TestCase):
    """Skill 契约创建/校验/序列化。"""

    def test_read_only_skill(self):
        skill = READ_ONLY_SKILL
        self.assertEqual(skill.skill_id, "skill:read_only")
        self.assertTrue(skill.can_perform("read"))
        self.assertFalse(skill.can_perform("write"))
        errors = skill.validate()
        self.assertEqual(errors, [])

    def test_write_skill(self):
        skill = WRITE_SKILL
        self.assertTrue(skill.can_perform("read"))
        self.assertTrue(skill.can_perform("write"))
        self.assertFalse(skill.can_perform("ratify"))

    def test_admin_skill(self):
        skill = ADMIN_SKILL
        self.assertTrue(skill.requires_founder_approval)
        self.assertTrue(skill.can_perform("ratify"))
        self.assertTrue(skill.can_perform("withdraw"))
        errors = skill.validate()
        self.assertEqual(errors, [])

    def test_invalid_skill_empty_id(self):
        skill = SkillContract(skill_id="", name="test")
        errors = skill.validate()
        self.assertTrue(any("skill_id" in e for e in errors))

    def test_ratify_without_approval_rejected(self):
        skill = SkillContract(
            skill_id="s1", name="test",
            allowed_operations={Operation.RATIFY.value},
            requires_founder_approval=False,
        )
        errors = skill.validate()
        self.assertTrue(any("founder_approval" in e for e in errors))

    def test_serialization_round_trip(self):
        skill = WRITE_SKILL
        j = skill.to_json()
        restored = SkillContract.from_dict(json.loads(j))
        self.assertEqual(restored.skill_id, skill.skill_id)
        self.assertEqual(restored.allowed_operations, skill.allowed_operations)

    def test_skill_registry(self):
        self.assertEqual(len(SKILL_REGISTRY), 3)
        self.assertIn("read_only", SKILL_REGISTRY)
        self.assertIn("admin", SKILL_REGISTRY)


class TestOperation(unittest.TestCase):
    """操作枚举。"""

    def test_four_operations(self):
        self.assertEqual(len(Operation), 4)


# ── 权限边界测试 ─────────────────────────────────────────────────────

class TestPermissionBoundary(unittest.TestCase):
    """权限边界与双闸模型。"""

    def test_default_boundary(self):
        boundary = DEFAULT_BOUNDARY
        self.assertTrue(boundary.ratify_requires_founder_approval)
        self.assertTrue(boundary.withdraw_requires_founder_approval)

    def test_scope_validation(self):
        boundary = PermissionBoundary()
        self.assertTrue(boundary.validate_scope("personal"))
        self.assertTrue(boundary.validate_scope("collaborative"))
        self.assertFalse(boundary.validate_scope("unknown"))

    def test_founder_approval_required(self):
        boundary = PermissionBoundary()
        self.assertTrue(boundary.requires_founder_approval("ratify"))
        self.assertTrue(boundary.requires_founder_approval("withdraw"))
        self.assertFalse(boundary.requires_founder_approval("read"))
        self.assertFalse(boundary.requires_founder_approval("write"))

    def test_permission_hierarchy(self):
        # WRITE 包含 READ
        self.assertTrue(DEFAULT_BOUNDARY.check_permission(
            PermissionLevel.WRITE, PermissionLevel.READ))
        # READ 不包含 WRITE
        self.assertFalse(DEFAULT_BOUNDARY.check_permission(
            PermissionLevel.READ, PermissionLevel.WRITE))
        # WITHDRAW 包含全部
        self.assertTrue(DEFAULT_BOUNDARY.check_permission(
            PermissionLevel.WITHDRAW, PermissionLevel.RATIFY))

    def test_none_permission(self):
        self.assertFalse(DEFAULT_BOUNDARY.check_permission(
            PermissionLevel.NONE, PermissionLevel.READ))


class TestScopeTag(unittest.TestCase):
    """Scope 标签枚举。"""

    def test_two_scopes(self):
        self.assertEqual(len(ScopeTag), 2)


class TestPermissionLevel(unittest.TestCase):
    """权限级别枚举。"""

    def test_five_levels(self):
        self.assertEqual(len(PermissionLevel), 5)


if __name__ == "__main__":
    unittest.main()
