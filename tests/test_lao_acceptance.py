"""P1 lao_acceptance 编排器测试（LAO 架构重构·2026-08-19）。

覆盖规格 10 个用例: 出站全通过/命中直返/预算降级/token警告/fail-open
                         回站全通过/事实编造reject/认知矛盾reject/成本萃取/fail-open
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.lao_acceptance import (  # noqa: E402
    OutboundChecker, InboundValidator, OutboundDecision, InboundDecision,
)
from lao.effect_anchored.experience_matching import ExperienceMatcher  # noqa: E402
from lao.effect_anchored.cost_intelligence import CostIntelligence  # noqa: E402
from lao.effect_anchored.recovery_budget import RecoveryBudget  # noqa: E402
from lao.effect_anchored.experience_extractor import ExperienceExtractor  # noqa: E402
from lao.effect_anchored.retry_counter import RetryCounter  # noqa: E402
from lao.effect_anchored.cognitive_system import DeterministicCognitiveSystem  # noqa: E402


def _request(task_text="测试问题", task_type="fact", agent="tristan",
             model="deepseek-v4-flash", ctx=1000):
    return {
        "request_id": "req-1",
        "request_features": {
            "task_text": task_text, "task_type": task_type,
            "agent_id": agent, "model_used": model,
            "provider_used": "deepseek", "context_tokens": ctx,
        },
        "response_features": {
            "response_text": "这是回答。", "response_tokens": 200,
            "cache_hit": False, "actual_cost": 0.001,
        },
        "verification": {},
    }


class TestOutboundPass(unittest.TestCase):
    """A1: 出站全通过"""

    def test_all_pass(self):
        matcher = ExperienceMatcher()
        matcher.EXPERIENCE_STORE = os.path.join(tempfile.mkdtemp(), "none.jsonl")
        oc = OutboundChecker(matcher=matcher, anchor_store=None,
                             budget=RecoveryBudget(max_cost=1.0),
                             cost=CostIntelligence())
        d = oc.check(_request())
        self.assertTrue(d.passed)
        self.assertEqual(len(d.steps), 4)
        self.assertIsNone(d.downgrade)


class TestOutboundDirectReturn(unittest.TestCase):
    """A2: 经验命中 direct_return"""

    def test_direct_return(self):
        import hashlib, json
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "exp.jsonl")
        fp = hashlib.sha256("测试问题".encode("utf-8")).hexdigest()[:16]
        os.makedirs(tmp, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "experience_id": f"exp-{fp}", "experience_fingerprint": fp,
                "experience_content": "Q: 测试问题\nA: 答案", "task_type": "fact",
                "agent_id": "tristan", "quality_grade": "verified",
                "isolation_key": "tristan"}, ensure_ascii=False) + "\n")
        matcher = ExperienceMatcher()
        matcher.EXPERIENCE_STORE = path
        oc = OutboundChecker(matcher=matcher)
        d = oc.check(_request())
        self.assertEqual(d.steps[0]["status"], "direct_return")


class TestOutboundBudgetOverrun(unittest.TestCase):
    """A3: 预算超 → 建议降级"""

    def test_downgrade(self):
        b = RecoveryBudget(max_cost=0.5)
        b.add_cost(0.49)
        # expected_cost 需 > remaining(0.01) → 用超大 context 触发
        oc = OutboundChecker(budget=b, cost=CostIntelligence())
        d = oc.check(_request(model="deepseek-v4-pro", ctx=200000))
        self.assertTrue(d.passed)
        self.assertEqual(d.downgrade, "flash")


class TestOutboundTokenWarn(unittest.TestCase):
    """A4: token 不合理 → warning"""

    def test_warn(self):
        oc = OutboundChecker(cost=CostIntelligence())
        d = oc.check(_request(task_type="chat", model="deepseek-v4-pro"))
        self.assertTrue(d.passed)
        self.assertTrue(any(s["status"] == "warning"
                            for s in d.steps if s["step"] == "token_efficiency"))


class TestOutboundFailOpen(unittest.TestCase):
    """A5: fail-open"""

    def test_fail_open(self):
        oc = OutboundChecker(matcher=None, anchor_store=None, budget=None, cost=None)
        d = oc.check(_request())
        self.assertTrue(d.passed)
        statuses = [s["status"] for s in d.steps]
        self.assertEqual(statuses, ["not_configured"] * 4)


class TestInboundPass(unittest.TestCase):
    """A6: 回站全通过"""

    def test_pass(self):
        tmp = tempfile.mkdtemp()
        extractor = ExperienceExtractor(store_path=os.path.join(tmp, "exp.jsonl"))
        iv = InboundValidator(
            reality=None, cognitive=DeterministicCognitiveSystem(),
            cost=CostIntelligence(), extractor=extractor,
            retry_counter=RetryCounter())
        d = iv.validate(_request(), _request())
        self.assertTrue(d.passed)
        self.assertFalse(d.reject)
        self.assertEqual(d.quality_grade, "verified")
        # 经验应被萃取
        self.assertTrue(os.path.exists(extractor.store_path))


class TestInboundReject(unittest.TestCase):
    """A7: 事实编造 → reject"""

    def test_reject(self):
        iv = InboundValidator(reality=None, cognitive=None, cost=None,
                              extractor=None, retry_counter=RetryCounter())
        req = _request()
        # 事实未验证 → 应 reject(规格 1.2 Step1: 事实编造 → REJECT_RESPONSE)
        req["verification"] = {"fact_verified": False}
        d = iv.validate(req, _request())
        self.assertTrue(d.reject)
        self.assertEqual(d.quality_grade, "disputed")


class TestInboundCognitiveConflict(unittest.TestCase):
    """A8: 认知严重矛盾 → reject"""

    def test_conflict(self):
        cog = DeterministicCognitiveSystem()
        # 植入锚点: 原则含"禁止欺骗"
        cog.add_anchor(user_id="tristan", principle="禁止欺骗客户",
                       domain="cognitive")
        iv = InboundValidator(cognitive=cog)
        req = _request(task_text="怎么欺骗客户？")
        resp = _request()
        resp["response_features"]["response_text"] = "可以欺骗客户。"
        d = iv.validate(req, resp)
        self.assertTrue(d.reject)


class TestInboundSettleAndExtract(unittest.TestCase):
    """A9: 成本核算+经验萃取"""

    def test_settle_extract(self):
        tmp = tempfile.mkdtemp()
        extractor = ExperienceExtractor(store_path=os.path.join(tmp, "exp.jsonl"))
        cost = CostIntelligence()
        iv = InboundValidator(cost=cost, extractor=extractor)
        req = _request()
        req["verification"] = {"fact_verified": True, "cognitive_consistent": True,
                               "retry_count": 0}
        resp = _request()
        resp["response_features"]["actual_cost"] = 0.002
        d = iv.validate(req, resp)
        self.assertTrue(d.passed)
        rows = extractor.load_all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["quality_grade"], "verified")
        self.assertEqual(cost.stats()["total_calls"], 1)


class TestInboundFailOpen(unittest.TestCase):
    """A10: 回站 fail-open"""

    def test_fail_open(self):
        iv = InboundValidator(reality=None, cognitive=None, cost=None,
                              extractor=None, retry_counter=None)
        d = iv.validate({}, {})
        self.assertTrue(d.passed)
        statuses = [s["status"] for s in d.steps]
        self.assertEqual(statuses, ["not_configured"] * 4)


if __name__ == "__main__":
    unittest.main()
