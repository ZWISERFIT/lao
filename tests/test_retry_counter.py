"""P0-2 retry_counter 测试（LAO 架构重构·2026-08-19）。

覆盖规格 6 个用例: init/increment/should_retry/clear/summary/上限。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.retry_counter import RetryCounter  # noqa: E402


class TestInit(unittest.TestCase):
    """R1: init → get == 0"""

    def test_init(self):
        c = RetryCounter()
        self.assertEqual(c.init("req-1"), 0)
        self.assertEqual(c.get("req-1"), 0)


class TestIncrement(unittest.TestCase):
    """R2: increment 3 次 → get == 3"""

    def test_increment(self):
        c = RetryCounter()
        c.init("req-1")
        self.assertEqual(c.increment("req-1"), 1)
        self.assertEqual(c.increment("req-1"), 2)
        self.assertEqual(c.increment("req-1"), 3)
        self.assertEqual(c.get("req-1"), 3)


class TestShouldRetry(unittest.TestCase):
    """R3: should_retry 0/1/2 → True·3 → False"""

    def test_should_retry(self):
        c = RetryCounter()
        c.init("req-1")
        self.assertTrue(c.should_retry("req-1"))
        c.increment("req-1")
        self.assertTrue(c.should_retry("req-1"))
        c.increment("req-1")
        self.assertTrue(c.should_retry("req-1"))
        c.increment("req-1")
        self.assertFalse(c.should_retry("req-1"))


class TestClear(unittest.TestCase):
    """R4: clear 后 get == 0·summary active 减 1"""

    def test_clear(self):
        c = RetryCounter()
        c.init("req-1")
        c.increment("req-1")
        c.clear("req-1")
        self.assertEqual(c.get("req-1"), 0)
        self.assertEqual(c.summary()["active_requests"], 0)


class TestSummary(unittest.TestCase):
    """R5: summary 统计"""

    def test_summary(self):
        c = RetryCounter()
        c.init("req-1")
        c.increment("req-1")
        c.increment("req-1")
        c.init("req-2")
        s = c.summary()
        self.assertEqual(s["active_requests"], 2)
        self.assertEqual(s["total_retries"], 2)


class TestMaxCap(unittest.TestCase):
    """R6: increment 超上限不超 MAX_RETRIES"""

    def test_max_cap(self):
        c = RetryCounter()
        c.init("req-1")
        for _ in range(5):
            c.increment("req-1")
        self.assertEqual(c.get("req-1"), c.MAX_RETRIES)
        self.assertEqual(c.get("req-1"), 3)


if __name__ == "__main__":
    unittest.main()
