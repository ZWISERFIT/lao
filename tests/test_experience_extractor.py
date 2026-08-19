"""P0-2 experience_extractor 测试（LAO 架构重构·2026-08-19）。

覆盖规格 10 个用例: 萃取/去重/密钥排除/限长/评级/指纹/隔离键/读取/去重/fail-open。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.experience_extractor import (  # noqa: E402
    ExperienceExtractor, ExperienceRecord,
)


def _record(task_text="什么是机器学习？", agent_id="shuyu", task_type="fact",
            response="机器学习是让计算机从数据中学习的领域。",
            fact_verified=True, cog_consistent=True, retry=0,
            model="deepseek-v4-flash", provider="aliyun-direct",
            cache_hit=False, cost=0.003):
    return {
        "request_id": "req-1",
        "request_features": {
            "task_text": task_text, "task_type": task_type,
            "agent_id": agent_id, "model_used": model,
            "provider_used": provider, "context_tokens": 1500,
        },
        "response_features": {
            "response_text": response, "response_tokens": 200,
            "cache_hit": cache_hit, "actual_cost": cost,
        },
        "verification": {
            "fact_verified": fact_verified,
            "cognitive_consistent": cog_consistent,
            "retry_count": retry,
        },
    }


class TestExtractBasic(unittest.TestCase):
    """T1: 基础萃取"""

    def test_extract_creates_record(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "lao_experiences.jsonl")
        ex = ExperienceExtractor(store_path=path)
        eid = ex.extract(_record())
        self.assertIsNotNone(eid)
        self.assertTrue(eid.startswith("exp-"))
        self.assertTrue(os.path.exists(path))
        with open(path, encoding="utf-8") as f:
            lines = [ln for ln in f if ln.strip()]
        self.assertEqual(len(lines), 1)


class TestDedupeOnSave(unittest.TestCase):
    """T2: 同 fingerprint+agent_id 二次 extract 覆盖"""

    def test_same_fp_overwrites(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "lao_experiences.jsonl")
        ex = ExperienceExtractor(store_path=path)
        ex.extract(_record(response="第一个回答。"))
        ex.extract(_record(response="第二个回答。"))
        with open(path, encoding="utf-8") as f:
            lines = [ln for ln in f if ln.strip()]
        self.assertEqual(len(lines), 1, "应覆盖为 1 行")
        import json
        d = json.loads(lines[0])
        self.assertIn("第二个回答", d["experience_content"])


class TestSecretExcluded(unittest.TestCase):
    """T3: 密钥内容请求不萃取"""

    def test_secret_request_skipped(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "lao_experiences.jsonl")
        ex = ExperienceExtractor(store_path=path)
        eid = ex.extract(_record(task_text="apiKey: sk-abcdef123456 是什么"))
        self.assertIsNone(eid)
        self.assertFalse(os.path.exists(path) or
                         (os.path.exists(path) and os.path.getsize(path) == 0))


class TestContentLimit(unittest.TestCase):
    """T4: content > 500 chars 截断"""

    def test_content_truncated(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "lao_experiences.jsonl")
        ex = ExperienceExtractor(store_path=path)
        long_resp = "机器学习是AI的核心。\n" + "x" * 800
        ex.extract(_record(response=long_resp))
        import json
        rows = ex.load_all()
        self.assertLessEqual(len(rows[0]["experience_content"]),
                             ex.MAX_CONTENT_CHARS)


class TestGrade(unittest.TestCase):
    """T5: verification 组合 → quality_grade 正确"""

    def test_verified(self):
        ex = ExperienceExtractor()
        self.assertEqual(
            ex._grade({"fact_verified": True, "cognitive_consistent": True,
                       "retry_count": 0}), "verified")

    def test_pending(self):
        ex = ExperienceExtractor()
        self.assertEqual(
            ex._grade({"fact_verified": True, "cognitive_consistent": True,
                       "retry_count": 2}), "pending")

    def test_disputed(self):
        ex = ExperienceExtractor()
        self.assertEqual(
            ex._grade({"fact_verified": False, "retry_count": 0}), "disputed")


class TestFingerprint(unittest.TestCase):
    """T6: fingerprint = sha256[:16]"""

    def test_fingerprint_len(self):
        ex = ExperienceExtractor()
        fp = ex._extract_fingerprint("test")
        self.assertEqual(len(fp), 16)
        import hashlib
        self.assertEqual(fp, hashlib.sha256(b"test").hexdigest()[:16])


class TestIsolationKey(unittest.TestCase):
    """T7: 隔离键 isolation_key == agent_id"""

    def test_isolation_matches(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "lao_experiences.jsonl")
        ex = ExperienceExtractor(store_path=path)
        ex.extract(_record(agent_id="zeus"))
        rows = ex.load_all()
        self.assertEqual(rows[0]["isolation_key"], "zeus")
        self.assertEqual(rows[0]["agent_id"], "zeus")


class TestLoadAll(unittest.TestCase):
    """T8: load_all 返回全部记录"""

    def test_load_all_count(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "lao_experiences.jsonl")
        ex = ExperienceExtractor(store_path=path)
        ex.extract(_record(task_text="问题一", response="答案一"))
        ex.extract(_record(task_text="问题二", response="答案二"))
        rows = ex.load_all()
        self.assertEqual(len(rows), 2)


class TestDedupe(unittest.TestCase):
    """T9: dedupe 去重"""

    def test_dedupe_removes_dup(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "lao_experiences.jsonl")
        ex = ExperienceExtractor(store_path=path)
        ex.extract(_record(response="旧"))
        ex.extract(_record(response="新"))
        removed = ex.dedupe()
        rows = ex.load_all()
        self.assertEqual(len(rows), 1)
        self.assertIn("新", rows[0]["experience_content"])


class TestFailOpen(unittest.TestCase):
    """T10: 无效 record 不抛"""

    def test_invalid_record(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "lao_experiences.jsonl")
        ex = ExperienceExtractor(store_path=path)
        self.assertIsNone(ex.extract({}))
        self.assertIsNone(ex.extract(None))
        self.assertEqual(ex.load_all(), [])


if __name__ == "__main__":
    unittest.main()
