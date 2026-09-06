"""T2 统一任务身份模块 — 单元测试（14 项，全部合成数据）。

P1-A T2 规格落地（143-T2-Design 第八节）：
    - UT-01 ~ UT-14 逐项验证
    - 所有测试数据为合成数据，非真实运行数据
    - session_fp 使用合法 SHA-256 hex（64 位小写十六进制）

运行方式：
    python -m pytest task_identity/test_task_identity.py -v
    或：
    python -m unittest task_identity.test_task_identity -v
"""

from __future__ import annotations

import json
import re
import unittest
from datetime import datetime, timezone

# 模块内 import
from task_identity.schema import (
    TaskIdentity,
    TASK_IDENTITY_SCHEMA,
    _UUID_RE,
    _FP_RE,
    _CORRELATION_RE,
)
from task_identity.fingerprint import compute_session_fingerprint
from task_identity.isolation import IsolationManager

# ── 合成数据常量 ─────────────────────────────────────────────────────
# 所有 session_fp 均为合法 SHA-256 hex（64 位小写十六进制）
# 注意：不得使用含 'g' 等非 hex 字符的字符串（历史 bug 教训）

SYNTHETIC_FP_1 = "a" * 64  # 合法 64 位 hex
SYNTHETIC_FP_2 = "b" * 64  # 合法 64 位 hex，与 FP_1 不同
SYNTHETIC_FP_3 = "0123456789abcdef" * 4  # 合法 64 位 hex（循环模式）
SYNTHETIC_UUID = "550e8400-e29b-41d4-a716-446655440000"  # 合法 UUID v4

SYNTHETIC_MESSAGES_MAIN = [
    {"role": "system", "content": "你是门店运营助手"},
    {"role": "user", "content": "查询门店 store-001 的出勤数据"},
]

SYNTHETIC_MESSAGES_SIDE = [
    {"role": "system", "content": "你是门店运营助手"},
    {"role": "user", "content": "提取 store-001 出勤表详细数据"},
]


class TestUT01MainTaskCreation(unittest.TestCase):
    """UT-01: TaskIdentity 创建（主任务）。"""

    def test_main_task_fields(self):
        identity = TaskIdentity.create(
            session_fp=SYNTHETIC_FP_1,
            agent="momo",
        )
        self.assertTrue(_UUID_RE.match(identity.task_id))
        self.assertEqual(identity.scope, "main")
        self.assertIsNone(identity.parent_task_id)
        self.assertEqual(identity.agent, "momo")
        self.assertFalse(identity.intent_locked)
        self.assertEqual(identity.session_fp, SYNTHETIC_FP_1)
        self.assertTrue(identity.correlation_id.startswith("corr-"))
        self.assertEqual(identity.metadata, {})
        errors = identity.validate()
        self.assertEqual(errors, [], f"校验失败: {errors}")


class TestUT02SideTaskCreation(unittest.TestCase):
    """UT-02: TaskIdentity 创建（侧任务）。"""

    def test_side_task_fields(self):
        identity = TaskIdentity.create(
            session_fp=SYNTHETIC_FP_2,
            parent_task_id=SYNTHETIC_UUID,
            agent="momo",
        )
        self.assertEqual(identity.scope, "side")
        self.assertEqual(identity.parent_task_id, SYNTHETIC_UUID)
        errors = identity.validate()
        self.assertEqual(errors, [], f"校验失败: {errors}")


class TestUT03FingerprintFormat(unittest.TestCase):
    """UT-03: session_fp 格式（64 位小写 hex，SHA-256）。"""

    def test_fingerprint_format(self):
        fp = compute_session_fingerprint(SYNTHETIC_MESSAGES_MAIN)
        self.assertEqual(len(fp), 64)
        self.assertTrue(_FP_RE.match(fp), f"session_fp 非合法 hex: {fp}")
        self.assertEqual(fp, fp.lower())  # 必须小写


class TestUT04FingerprintStability(unittest.TestCase):
    """UT-04: session_fp 稳定性（相同输入 → 相同输出）。"""

    def test_same_input_same_fp(self):
        fp1 = compute_session_fingerprint(SYNTHETIC_MESSAGES_MAIN)
        fp2 = compute_session_fingerprint(SYNTHETIC_MESSAGES_MAIN)
        self.assertEqual(fp1, fp2)


class TestUT05FingerprintDistinctness(unittest.TestCase):
    """UT-05: session_fp 区分性（不同输入 → 不同输出）。"""

    def test_different_input_different_fp(self):
        fp1 = compute_session_fingerprint(SYNTHETIC_MESSAGES_MAIN)
        fp2 = compute_session_fingerprint(SYNTHETIC_MESSAGES_SIDE)
        self.assertNotEqual(fp1, fp2)


class TestUT06CorrelationIdFormat(unittest.TestCase):
    """UT-06: correlation_id 格式。"""

    def test_correlation_id_format(self):
        identity = TaskIdentity.create(session_fp=SYNTHETIC_FP_1)
        self.assertTrue(
            _CORRELATION_RE.match(identity.correlation_id),
            f"correlation_id 格式非法: {identity.correlation_id}",
        )
        # 格式: corr-{YYYYMMDD}-{task_id前8位}
        parts = identity.correlation_id.split("-")
        self.assertEqual(len(parts), 3)
        self.assertEqual(parts[0], "corr")
        self.assertEqual(len(parts[1]), 8)  # YYYYMMDD
        self.assertEqual(len(parts[2]), 8)  # task_id 前 8 位


class TestUT07IntentLockedDefault(unittest.TestCase):
    """UT-07: intent_locked 默认值。"""

    def test_default_false(self):
        identity = TaskIdentity.create(session_fp=SYNTHETIC_FP_1)
        self.assertFalse(identity.intent_locked)

    def test_explicit_true(self):
        identity = TaskIdentity.create(
            session_fp=SYNTHETIC_FP_1, intent_locked=True
        )
        self.assertTrue(identity.intent_locked)


class TestUT08ScopeResolution(unittest.TestCase):
    """UT-08: IsolationManager.scope 判定。"""

    def test_main_scope(self):
        self.assertEqual(IsolationManager.resolve_scope(None), "main")

    def test_side_scope(self):
        self.assertEqual(IsolationManager.resolve_scope(SYNTHETIC_UUID), "side")


class TestUT09CostIsolation(unittest.TestCase):
    """UT-09: IsolationManager 成本隔离。"""

    def test_side_isolated(self):
        side = TaskIdentity.create(
            session_fp=SYNTHETIC_FP_1, parent_task_id=SYNTHETIC_UUID
        )
        self.assertTrue(IsolationManager.should_isolate_cost(side))

    def test_main_not_isolated(self):
        main = TaskIdentity.create(session_fp=SYNTHETIC_FP_1)
        self.assertFalse(IsolationManager.should_isolate_cost(main))


class TestUT10MemoryIsolation(unittest.TestCase):
    """UT-10: IsolationManager 记忆隔离。"""

    def test_side_isolated(self):
        side = TaskIdentity.create(
            session_fp=SYNTHETIC_FP_1, parent_task_id=SYNTHETIC_UUID
        )
        self.assertTrue(IsolationManager.should_isolate_memory(side))

    def test_main_not_isolated(self):
        main = TaskIdentity.create(session_fp=SYNTHETIC_FP_1)
        self.assertFalse(IsolationManager.should_isolate_memory(main))


class TestUT11ResultWrite(unittest.TestCase):
    """UT-11: IsolationManager 结果写入。"""

    def test_main_can_write(self):
        main = TaskIdentity.create(session_fp=SYNTHETIC_FP_1)
        self.assertTrue(IsolationManager.can_write_result(main))

    def test_side_cannot_write(self):
        side = TaskIdentity.create(
            session_fp=SYNTHETIC_FP_1, parent_task_id=SYNTHETIC_UUID
        )
        self.assertFalse(IsolationManager.can_write_result(side))


class TestUT12SchemaValidation(unittest.TestCase):
    """UT-12: JSON Schema 校验（合法 TaskIdentity 通过）。"""

    def test_valid_identity_passes(self):
        identity = TaskIdentity.create(
            session_fp=SYNTHETIC_FP_1,
            agent="momo",
            metadata={"objective": "合成测试"},
        )
        errors = identity.validate()
        self.assertEqual(errors, [])
        # 序列化 → 反序列化 → 再校验
        json_str = identity.to_json()
        data = json.loads(json_str)
        restored = TaskIdentity.from_dict(data)
        errors2 = restored.validate()
        self.assertEqual(errors2, [])
        self.assertEqual(identity.task_id, restored.task_id)
        self.assertEqual(identity.session_fp, restored.session_fp)


class TestUT13SchemaRejection(unittest.TestCase):
    """UT-13: JSON Schema 拒绝非法值。"""

    def test_invalid_fp_rejected(self):
        # session_fp 含非 hex 字符 'g'
        with self.assertRaises(ValueError):
            TaskIdentity.create(session_fp="g" * 64)

    def test_short_fp_rejected(self):
        # session_fp 长度不足
        with self.assertRaises(ValueError):
            TaskIdentity.create(session_fp="a" * 16)

    def test_invalid_parent_uuid_rejected(self):
        # parent_task_id 非合法 UUID
        with self.assertRaises(ValueError):
            TaskIdentity.create(
                session_fp=SYNTHETIC_FP_1, parent_task_id="not-a-uuid"
            )

    def test_validate_catches_bad_scope(self):
        # 直接构造非法对象（绕过工厂方法）
        bad = TaskIdentity(
            task_id="550e8400-e29b-41d4-a716-446655440000",
            session_fp=SYNTHETIC_FP_1,
            scope="invalid_scope",
        )
        errors = bad.validate()
        self.assertTrue(any("scope" in e for e in errors))


class TestUT14BackwardCompat(unittest.TestCase):
    """UT-14: 向后兼容 — 旧 16 位 session_fp 不阻塞新代码。"""

    def test_old_fp_not_accepted_by_factory(self):
        # 旧 16 位 SHA1 指纹不应被新工厂方法接受
        old_fp = "a1b2c3d4e5f6a7b8"  # 16 位
        with self.assertRaises(ValueError):
            TaskIdentity.create(session_fp=old_fp)

    def test_new_fp_always_64_chars(self):
        # 新指纹始终为 64 位
        fp = compute_session_fingerprint(SYNTHETIC_MESSAGES_MAIN)
        self.assertEqual(len(fp), 64)
        # 空 messages 也返回合法 64 位 hex
        fp_empty = compute_session_fingerprint([])
        self.assertEqual(len(fp_empty), 64)
        self.assertTrue(_FP_RE.match(fp_empty))


if __name__ == "__main__":
    unittest.main()
