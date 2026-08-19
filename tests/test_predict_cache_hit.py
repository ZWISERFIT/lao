"""P1 predict_cache_hit 测试（LAO 架构重构·2026-08-19·Stella 修正1: 新建方法）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.cognitive_anchor import (  # noqa: E402
    CognitiveAnchorStore, DecisionAnchor,
)


class TestPredictCacheHit(unittest.TestCase):
    """P1/P2/P3: 命中率预测"""

    def test_hit_when_anchor_exists(self):
        store = CognitiveAnchorStore(max_anchors=100)
        store.put(DecisionAnchor(
            anchor_id="d1", anchor_type="decision",
            value={"trigger_condition": "退款超过500", "principle": "客户信任优先",
                   "action_rule": "人工介入"},
        ))
        r = store.predict_cache_hit("退款超过500")
        self.assertTrue(r["cache_hit"])
        self.assertGreaterEqual(r["confidence"], 0.0)
        self.assertGreaterEqual(r["anchors"], 1)

    def test_miss_when_no_anchor(self):
        store = CognitiveAnchorStore(max_anchors=100)
        r = store.predict_cache_hit("完全不相关的问题xyz")
        self.assertFalse(r["cache_hit"])
        self.assertEqual(r["anchors"], 0)

    def test_fail_open(self):
        store = CognitiveAnchorStore(max_anchors=100)
        r = store.predict_cache_hit("")  # 空 trigger 不应抛
        self.assertIn("cache_hit", r)


if __name__ == "__main__":
    unittest.main()
