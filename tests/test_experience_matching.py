"""P0 experience_matching 升级测试（LAO 架构重构·2026-08-19）。

覆盖规格 10 个用例: 空库/精确verified/精确pending/disputed跳过/语义/隔离键/task_type/fail-open/兼容/阈值。
"""
import os
import sys
import json
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.experience_matching import (  # noqa: E402
    ExperienceMatcher, MatchResult,
)


def _write_store(path, rows):
    """写经验库(测试用·覆盖 EXperienceMatcher.EXPERIENCE_STORE 指向)。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _exp(fingerprint, agent="tristan", task_type="fact", grade="verified",
         content="Q: 测试\nA: 结论"):
    return {
        "experience_id": f"exp-{fingerprint}",
        "experience_fingerprint": fingerprint,
        "experience_content": content,
        "task_type": task_type,
        "agent_id": agent,
        "quality_grade": grade,
        "isolation_key": agent,
    }


class TestEmptyStore(unittest.TestCase):
    """M1: 无经验库 → proceed_to_llm"""

    def test_empty(self):
        tmp = tempfile.mkdtemp()
        matcher = ExperienceMatcher()
        matcher.EXPERIENCE_STORE = os.path.join(tmp, "nope.jsonl")
        r = matcher.match({"task_text": "问题", "agent_id": "tristan", "task_type": "fact"})
        self.assertEqual(r.action, "proceed_to_llm")
        self.assertLess(r.confidence, 0.5)
        self.assertIsNone(r.matched_experience)


class TestExactVerified(unittest.TestCase):
    """M2: 精确指纹命中 verified → direct_return"""

    def test_exact_verified(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "exp.jsonl")
        import hashlib
        fp = hashlib.sha256("问题A".encode("utf-8")).hexdigest()[:16]
        _write_store(path, [_exp(fp, grade="verified")])
        matcher = ExperienceMatcher()
        matcher.EXPERIENCE_STORE = path
        r = matcher.match({"task_text": "问题A", "agent_id": "tristan", "task_type": "fact"})
        self.assertEqual(r.confidence, 1.0)
        self.assertEqual(r.action, "direct_return")
        self.assertEqual(r.matched_via, "exact")


class TestExactPending(unittest.TestCase):
    """M3: 精确指纹命中 pending → flag_for_comparison"""

    def test_exact_pending(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "exp.jsonl")
        import hashlib
        fp = hashlib.sha256("问题B".encode("utf-8")).hexdigest()[:16]
        _write_store(path, [_exp(fp, grade="pending")])
        matcher = ExperienceMatcher()
        matcher.EXPERIENCE_STORE = path
        r = matcher.match({"task_text": "问题B", "agent_id": "tristan", "task_type": "fact"})
        self.assertEqual(r.confidence, 0.6)
        self.assertEqual(r.action, "flag_for_comparison")


class TestDisputedSkipped(unittest.TestCase):
    """M4: disputed 经验跳过"""

    def test_disputed(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "exp.jsonl")
        import hashlib
        fp = hashlib.sha256("问题C".encode("utf-8")).hexdigest()[:16]
        _write_store(path, [_exp(fp, grade="disputed")])
        matcher = ExperienceMatcher()
        matcher.EXPERIENCE_STORE = path
        r = matcher.match({"task_text": "问题C", "agent_id": "tristan", "task_type": "fact"})
        self.assertEqual(r.action, "proceed_to_llm")
        self.assertIsNone(r.matched_experience)


class TestSemanticMatch(unittest.TestCase):
    """M5: 语义匹配(相似文本) → 灰区"""

    def test_semantic(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "exp.jsonl")
        _write_store(path, [_exp("a1b2c3d4e5f67890", grade="verified",
                                 content="Q: 如何优化API调用延迟\nA: 使用缓存和批量请求")])
        matcher = ExperienceMatcher()
        matcher.EXPERIENCE_STORE = path
        r = matcher.match({"task_text": "如何优化API调用延迟",
                           "agent_id": "tristan", "task_type": "fact"})
        self.assertIn(r.action, ("direct_return", "flag_for_comparison"))
        self.assertGreaterEqual(r.confidence, 0.4)


class TestIsolationViolation(unittest.TestCase):
    """M6: 隔离键违规 → isolation_violation=True → proceed_to_llm"""

    def test_isolation(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "exp.jsonl")
        import hashlib
        fp = hashlib.sha256("问题D".encode("utf-8")).hexdigest()[:16]
        _write_store(path, [_exp(fp, agent="zeus", grade="verified")])
        matcher = ExperienceMatcher()
        matcher.EXPERIENCE_STORE = path
        r = matcher.match({"task_text": "问题D", "agent_id": "tristan", "task_type": "fact"})
        self.assertTrue(r.isolation_violation)
        self.assertEqual(r.action, "proceed_to_llm")


class TestTaskTypeFilter(unittest.TestCase):
    """M7: 不同 task_type 不匹配(语义只在同 task_type 内)"""

    def test_task_type(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "exp.jsonl")
        _write_store(path, [_exp("a1b2c3d4e5f67890", agent="tristan",
                                 task_type="decision",
                                 content="Q: 如何优化API延迟\nA: 缓存批量")])
        matcher = ExperienceMatcher()
        matcher.EXPERIENCE_STORE = path
        r = matcher.match({"task_text": "如何优化API延迟",
                           "agent_id": "tristan", "task_type": "fact"})
        # task_type 不同 → 语义候选为空 → proceed_to_llm
        self.assertEqual(r.action, "proceed_to_llm")


class TestFailOpen(unittest.TestCase):
    """M8: fail-open 无 task_text → proceed_to_llm 不抛"""

    def test_fail_open(self):
        matcher = ExperienceMatcher()
        r = matcher.match({})
        self.assertEqual(r.action, "proceed_to_llm")
        r2 = matcher.match(None)
        self.assertEqual(r2.action, "proceed_to_llm")


class TestCompatRetrieve(unittest.TestCase):
    """M9: retrieve_verified_experience 保留(兼容性)"""

    def test_retrieve_kept(self):
        matcher = ExperienceMatcher()
        self.assertTrue(hasattr(matcher, "retrieve_verified_experience"))
        self.assertTrue(callable(matcher.retrieve_verified_experience))


class TestThreshold(unittest.TestCase):
    """M10: 阈值边界 0.85 → direct_return·0.50 → flag_for_comparison"""

    def test_thresholds(self):
        matcher = ExperienceMatcher()
        self.assertEqual(matcher._decide(0.85), "direct_return")
        self.assertEqual(matcher._decide(0.50), "flag_for_comparison")
        self.assertEqual(matcher._decide(0.49), "proceed_to_llm")
        self.assertEqual(matcher._decide(0.84), "flag_for_comparison")


if __name__ == "__main__":
    unittest.main()
