"""P1 cost_intelligence 测试（LAO 架构重构·2026-08-19）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.cost_intelligence import CostIntelligence  # noqa: E402


def _req(task_type="chat", model="deepseek-v4-pro", ctx=1000, resp_tok=200):
    return {
        "request_id": "r1",
        "request_features": {
            "task_text": "测试", "task_type": task_type, "agent_id": "tristan",
            "model_used": model, "provider_used": "deepseek", "context_tokens": ctx,
        },
        "response_features": {
            "response_text": "答案", "response_tokens": resp_tok,
            "cache_hit": False, "actual_cost": 0.0,
        },
    }


class TestTokenEfficiency(unittest.TestCase):
    """C1/C2/C5: Token 合理性"""

    def test_pro_simple_warns(self):
        c = CostIntelligence()
        r = c.check_token_efficiency(_req(task_type="chat", model="deepseek-v4-pro"))
        self.assertEqual(r["status"], "warning")
        self.assertIn("flash", r["detail"])

    def test_flash_fact_ok(self):
        c = CostIntelligence()
        r = c.check_token_efficiency(_req(task_type="fact", model="deepseek-v4-flash"))
        self.assertEqual(r["status"], "ok")

    def test_large_context_warns(self):
        c = CostIntelligence()
        r = c.check_token_efficiency(_req(ctx=20000))
        self.assertEqual(r["status"], "warning")
        self.assertIn("context_tokens", r["detail"])


class TestSettleAndLog(unittest.TestCase):
    """C3: 成本核算"""

    def test_settle(self):
        c = CostIntelligence()
        r = c.settle_and_log(_req(model="deepseek-v4-pro", ctx=1000, resp_tok=1000),
                             _req(model="deepseek-v4-pro"))
        self.assertEqual(r["status"], "ok")
        self.assertGreater(r["cost"], 0)
        s = c.stats()
        self.assertEqual(s["total_calls"], 1)
        self.assertAlmostEqual(s["total_cost"], r["cost"], places=6)

    def test_cache_hit_recorded(self):
        c = CostIntelligence()
        req = _req()
        resp = _req()
        resp["response_features"]["cache_hit"] = True
        r = c.settle_and_log(req, resp)
        self.assertTrue(r["cache_hit"])
        self.assertEqual(c.stats()["cache_hits"], 1)


class TestExpectedCost(unittest.TestCase):
    """C4: 期望成本计算"""

    def test_expected_pro(self):
        c = CostIntelligence()
        # ctx=1000·resp≈200·pro=0.003/1k → 1200/1000*0.003 = 0.0036
        cost = c.expected_cost(_req(model="deepseek-v4-pro", ctx=1000))
        self.assertAlmostEqual(cost, 0.0036, places=6)

    def test_expected_flash(self):
        c = CostIntelligence()
        cost = c.expected_cost(_req(model="deepseek-v4-flash", ctx=1000))
        self.assertAlmostEqual(cost, 0.0012, places=6)


class TestFailOpen(unittest.TestCase):
    def test_invalid(self):
        c = CostIntelligence()
        self.assertEqual(c.check_token_efficiency({})["status"], "ok")
        self.assertEqual(c.expected_cost({}), 0.0)
        self.assertEqual(c.settle_and_log({}, {})["status"], "ok")


if __name__ == "__main__":
    unittest.main()
