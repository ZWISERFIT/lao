"""T3 上下文剪枝与意图保持 — 单元测试（全部合成数据）。

P1-A T3 规格落地（149-T3-Design）：
    - ContextPruner 剪枝规则/保护机制
    - IntentKeeper 意图保持率计算
    - 合成对话样例验证

运行方式：
    python -m unittest context_pruning.test_context_pruning -v
"""

from __future__ import annotations

import unittest

from context_pruning.pruner import ContextPruner, PruningConfig, PROTECTED_ROLES
from context_pruning.intent_keeper import IntentKeeper, IntentItem


# ── 合成数据 ─────────────────────────────────────────────────────────

SYNTH_MESSAGES = [
    {"role": "system", "content": "你是门店运营助手，权限：只读。禁止删除数据。"},
    {"role": "user", "content": "查询门店 store-001 的出勤数据"},
    {"role": "assistant", "content": "好的，正在查询 store-001 的出勤数据..."},
    {"role": "user", "content": "请同时查询 store-002"},
    {"role": "assistant", "content": "store-002 出勤数据如下..."},
    {"role": "user", "content": "再查 store-003"},
    {"role": "assistant", "content": "store-003 出勤数据如下..."},
    {"role": "user", "content": "还有 store-004"},
    {"role": "assistant", "content": "store-004 出勤数据如下..."},
    {"role": "user", "content": "store-005 呢？"},
    {"role": "assistant", "content": "store-005 出勤数据如下..."},
    {"role": "user", "content": "最后查 store-006"},
    {"role": "assistant", "content": "store-006 出勤数据如下..."},
]

SYNTH_INTENTS = [
    {"intent_id": "i1", "text": "查询门店 store-001 的出勤数据"},
    {"intent_id": "i2", "text": "请同时查询 store-002"},
    {"intent_id": "i3", "text": "再查 store-003"},
    {"intent_id": "i4", "text": "还有 store-004"},
    {"intent_id": "i5", "text": "store-005 呢？"},
    {"intent_id": "i6", "text": "最后查 store-006"},
]


# ── ContextPruner 测试 ───────────────────────────────────────────────

class TestPrunerProtection(unittest.TestCase):
    """剪枝保护规则：system/工具/关键词不可剪枝。"""

    def test_system_always_protected(self):
        pruner = ContextPruner()
        msg = {"role": "system", "content": "你是助手"}
        self.assertTrue(pruner.is_protected(msg))

    def test_tool_call_protected(self):
        pruner = ContextPruner()
        msg = {"role": "assistant", "tool_calls": [{"id": "t1"}]}
        self.assertTrue(pruner.is_protected(msg))

    def test_tool_result_protected(self):
        pruner = ContextPruner()
        msg = {"role": "tool", "tool_call_id": "t1", "content": "结果"}
        self.assertTrue(pruner.is_protected(msg))

    def test_permission_keyword_protected(self):
        pruner = ContextPruner()
        msg = {"role": "user", "content": "我的权限是只读"}
        self.assertTrue(pruner.is_protected(msg))

    def test_normal_message_not_protected(self):
        pruner = ContextPruner()
        msg = {"role": "user", "content": "今天天气怎么样"}
        self.assertFalse(pruner.is_protected(msg))


class TestPrunerPruning(unittest.TestCase):
    """剪枝引擎：压缩更早历史，保留最近 N 轮。"""

    def test_short_conversation_no_pruning(self):
        pruner = ContextPruner(config=PruningConfig(keep_recent_turns=5))
        result = pruner.prune(SYNTH_MESSAGES[:4])
        self.assertEqual(result["compressed_count"], 0)
        self.assertEqual(result["pruned_count"], result["original_count"])

    def test_long_conversation_compressed(self):
        pruner = ContextPruner(config=PruningConfig(keep_recent_turns=2))
        result = pruner.prune(SYNTH_MESSAGES)
        self.assertGreater(result["compressed_count"], 0)
        self.assertLess(result["pruned_count"], result["original_count"])

    def test_system_preserved_after_pruning(self):
        pruner = ContextPruner(config=PruningConfig(keep_recent_turns=1))
        result = pruner.prune(SYNTH_MESSAGES)
        roles = [m["role"] for m in result["pruned_messages"]]
        self.assertIn("system", roles)

    def test_empty_messages(self):
        pruner = ContextPruner()
        result = pruner.prune([])
        self.assertEqual(result["pruned_count"], 0)
        self.assertEqual(result["original_count"], 0)

    def test_intent_preserved(self):
        pruner = ContextPruner(config=PruningConfig(keep_recent_turns=1))
        intent = "查询门店 store-001 的出勤数据"
        result = pruner.prune(SYNTH_MESSAGES[:4], intent=intent)
        # 意图应存在于剪枝后的消息中
        all_content = " ".join(m.get("content", "") for m in result["pruned_messages"])
        self.assertIn(intent, all_content)


class TestPruningConfig(unittest.TestCase):
    """剪枝配置校验。"""

    def test_default_valid(self):
        cfg = PruningConfig()
        self.assertEqual(cfg.validate(), [])

    def test_invalid_turns(self):
        cfg = PruningConfig(keep_recent_turns=0)
        errors = cfg.validate()
        self.assertTrue(len(errors) > 0)


class TestProtectedRoles(unittest.TestCase):
    """保护角色枚举。"""

    def test_system_in_protected(self):
        self.assertIn("system", PROTECTED_ROLES)


# ── IntentKeeper 测试 ────────────────────────────────────────────────

class TestIntentKeeper(unittest.TestCase):
    """意图保持率计算。"""

    def test_all_preserved(self):
        keeper = IntentKeeper()
        keeper.register_intents(SYNTH_INTENTS)
        # 所有意图都在剪枝内容中
        all_text = " ".join(i["text"] for i in SYNTH_INTENTS)
        preserved = keeper.check_preservation(all_text)
        self.assertEqual(preserved, 6)
        self.assertAlmostEqual(keeper.keep_rate(), 1.0)

    def test_partial_preserved(self):
        keeper = IntentKeeper()
        keeper.register_intents(SYNTH_INTENTS)
        # 只保留前 5 个意图
        partial_text = " ".join(i["text"] for i in SYNTH_INTENTS[:5])
        preserved = keeper.check_preservation(partial_text)
        self.assertEqual(preserved, 5)
        self.assertAlmostEqual(keeper.keep_rate(), 5 / 6)

    def test_none_preserved(self):
        keeper = IntentKeeper()
        keeper.register_intents(SYNTH_INTENTS)
        preserved = keeper.check_preservation("完全不相关的内容")
        self.assertEqual(preserved, 0)
        self.assertAlmostEqual(keeper.keep_rate(), 0.0)

    def test_meets_threshold(self):
        keeper = IntentKeeper()
        keeper.register_intents(SYNTH_INTENTS)
        # 保留 6/6 = 100% >= 95%
        all_text = " ".join(i["text"] for i in SYNTH_INTENTS)
        keeper.check_preservation(all_text)
        self.assertTrue(keeper.meets_threshold(0.95))

    def test_below_threshold(self):
        keeper = IntentKeeper()
        keeper.register_intents(SYNTH_INTENTS)
        # 保留 5/6 = 83.3% < 95%
        partial_text = " ".join(i["text"] for i in SYNTH_INTENTS[:5])
        keeper.check_preservation(partial_text)
        self.assertFalse(keeper.meets_threshold(0.95))

    def test_empty_intents(self):
        keeper = IntentKeeper()
        self.assertIsNone(keeper.keep_rate())
        self.assertFalse(keeper.meets_threshold())

    def test_report(self):
        keeper = IntentKeeper()
        keeper.register_intents(SYNTH_INTENTS[:3])
        all_text = " ".join(i["text"] for i in SYNTH_INTENTS[:3])
        keeper.check_preservation(all_text)
        report = keeper.report()
        self.assertEqual(report["total_intents"], 3)
        self.assertEqual(report["preserved_count"], 3)
        self.assertAlmostEqual(report["keep_rate"], 1.0)
        self.assertTrue(report["meets_threshold_95"])


class TestIntentItem(unittest.TestCase):
    """IntentItem 数据结构。"""

    def test_to_dict(self):
        item = IntentItem(intent_id="i1", text="合成意图", preserved=True)
        d = item.to_dict()
        self.assertEqual(d["intent_id"], "i1")
        self.assertEqual(d["text"], "合成意图")
        self.assertTrue(d["preserved"])


# ── 集成测试：剪枝 + 意图保持率 ──────────────────────────────────────

class TestPruningWithIntentKeeper(unittest.TestCase):
    """集成：剪枝后计算意图保持率。"""

    def test_full_pipeline(self):
        pruner = ContextPruner(config=PruningConfig(keep_recent_turns=2))
        keeper = IntentKeeper()
        keeper.register_intents(SYNTH_INTENTS)

        result = pruner.prune(SYNTH_MESSAGES)
        pruned_content = " ".join(
            m.get("content", "") for m in result["pruned_messages"]
        )
        preserved = keeper.check_preservation(pruned_content)
        rate = keeper.keep_rate()

        self.assertIsNotNone(rate)
        self.assertGreaterEqual(rate, 0.0)
        self.assertLessEqual(rate, 1.0)
        # 受保护消息（system）+ 最近 2 轮应保留大部分意图
        self.assertGreaterEqual(result["protected_count"], 1)


if __name__ == "__main__":
    unittest.main()
