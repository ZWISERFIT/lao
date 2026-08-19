"""P1 check_budget 测试（LAO 架构重构·2026-08-19）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.recovery_budget import RecoveryBudget  # noqa: E402


class TestCheckBudget(unittest.TestCase):
    """B1/B2: 成本红线"""

    def test_within_budget(self):
        b = RecoveryBudget(max_cost=1.0)
        b.add_cost(0.2)
        r = b.check_budget(expected_cost=0.3)
        self.assertEqual(r["status"], "ok")
        self.assertFalse(r["suggest_downgrade"])
        self.assertAlmostEqual(r["budget_remaining"], 0.8, places=4)

    def test_overrun(self):
        b = RecoveryBudget(max_cost=1.0)
        b.add_cost(0.9)
        r = b.check_budget(expected_cost=0.3)
        self.assertEqual(r["status"], "overrun")
        self.assertTrue(r["suggest_downgrade"])

    def test_no_cap_fail_open(self):
        b = RecoveryBudget()  # max_cost=0 → 不限
        r = b.check_budget(expected_cost=999.0)
        self.assertEqual(r["status"], "ok")


if __name__ == "__main__":
    unittest.main()
