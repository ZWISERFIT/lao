"""T9 匿名评测集与 A/B 评测 — 单元测试（全部合成数据）。

P1-A T9 规格落地（155-T9-Design）：
    - 30+ 合成评测样本生成
    - A/B 评测引擎
    - 评测报告生成

运行方式：
    python -m unittest evaluation_suite.test_evaluation_suite -v
"""

from __future__ import annotations

import json
import unittest

from evaluation_suite.dataset import (
    EvalItem,
    EvalDataset,
    generate_synthetic_dataset,
    DEFAULT_EVAL_DATASET,
)
from evaluation_suite.ab_engine import (
    EvalResult,
    RoundResult,
    ABTestResult,
    ABEngine,
)
from evaluation_suite.report import EvalReport


# ── 评测数据集测试 ───────────────────────────────────────────────────

class TestEvalDataset(unittest.TestCase):
    """评测数据集。"""

    def test_default_dataset_30_plus(self):
        """默认数据集满足 30+ 要求。"""
        self.assertGreaterEqual(len(DEFAULT_EVAL_DATASET), 30)

    def test_generate_custom_count(self):
        dataset = generate_synthetic_dataset(count=35)
        self.assertEqual(len(dataset), 35)

    def test_get_by_category(self):
        dataset = generate_synthetic_dataset(count=32)
        general_items = dataset.get_by_category("general")
        self.assertGreater(len(general_items), 0)

    def test_get_by_difficulty(self):
        dataset = generate_synthetic_dataset(count=32)
        easy_items = dataset.get_by_difficulty("easy")
        self.assertGreater(len(easy_items), 0)

    def test_serialization(self):
        d = DEFAULT_EVAL_DATASET.to_dict()
        self.assertEqual(d["item_count"], len(DEFAULT_EVAL_DATASET))
        j = DEFAULT_EVAL_DATASET.to_json()
        parsed = json.loads(j)
        self.assertEqual(parsed["item_count"], len(DEFAULT_EVAL_DATASET))


class TestEvalItem(unittest.TestCase):
    """评测样本。"""

    def test_to_dict(self):
        item = EvalItem(
            item_id="eval-001",
            task_description="合成任务",
            expected_provider="deepseek",
        )
        d = item.to_dict()
        self.assertEqual(d["item_id"], "eval-001")
        self.assertEqual(d["expected_provider"], "deepseek")


# ── A/B 评测引擎测试 ─────────────────────────────────────────────────

class TestABEngine(unittest.TestCase):
    """A/B 评测引擎。"""

    def test_simulate_baseline(self):
        engine = ABEngine(seed=42)
        item = EvalItem(
            item_id="eval-001",
            task_description="合成任务",
            expected_provider="deepseek",
        )
        result = engine.simulate_baseline(item)
        self.assertEqual(result.item_id, "eval-001")
        self.assertIn(result.actual_provider, ["deepseek", "qwen", "token-plan", "novarouteai"])

    def test_simulate_optimized(self):
        engine = ABEngine(seed=42)
        item = EvalItem(
            item_id="eval-001",
            task_description="合成任务",
            expected_provider="deepseek",
        )
        result = engine.simulate_optimized(item)
        self.assertEqual(result.item_id, "eval-001")

    def test_run_round(self):
        engine = ABEngine(seed=42)
        dataset = generate_synthetic_dataset(count=10)
        round_result = engine.run_round(dataset, strategy="baseline", round_id=1)
        self.assertEqual(round_result.round_id, 1)
        self.assertEqual(len(round_result.results), 10)

    def test_run_ab_test_3_rounds(self):
        engine = ABEngine(seed=42)
        dataset = generate_synthetic_dataset(count=32)
        ab_result = engine.run_ab_test(dataset, rounds=3)
        self.assertEqual(len(ab_result.rounds_a), 3)
        self.assertEqual(len(ab_result.rounds_b), 3)

    def test_ab_result_serialization(self):
        engine = ABEngine(seed=42)
        dataset = generate_synthetic_dataset(count=10)
        ab_result = engine.run_ab_test(dataset, rounds=2)
        d = ab_result.to_dict()
        self.assertEqual(d["strategy_a"], "baseline")
        self.assertEqual(d["strategy_b"], "optimized")


class TestRoundResult(unittest.TestCase):
    """单轮评测结果。"""

    def test_accuracy(self):
        results = [
            EvalResult("1", "deepseek", "deepseek", 0.01, 100, 0.9, True),
            EvalResult("2", "qwen", "deepseek", 0.01, 100, 0.9, False),
        ]
        round_result = RoundResult(round_id=1, results=results)
        self.assertAlmostEqual(round_result.accuracy, 0.5)

    def test_avg_cost(self):
        results = [
            EvalResult("1", "deepseek", "deepseek", 0.01, 100, 0.9, True),
            EvalResult("2", "deepseek", "deepseek", 0.03, 100, 0.9, True),
        ]
        round_result = RoundResult(round_id=1, results=results)
        self.assertAlmostEqual(round_result.avg_cost, 0.02)

    def test_empty_results(self):
        round_result = RoundResult(round_id=1, results=[])
        self.assertEqual(round_result.accuracy, 0.0)
        self.assertEqual(round_result.avg_cost, 0.0)


# ── 评测报告测试 ─────────────────────────────────────────────────────

class TestEvalReport(unittest.TestCase):
    """评测报告。"""

    def test_from_ab_test(self):
        engine = ABEngine(seed=42)
        dataset = generate_synthetic_dataset(count=32)
        ab_result = engine.run_ab_test(dataset, rounds=3)
        report = EvalReport.from_ab_test(
            dataset_name="synthetic_eval_v1",
            dataset_version="1.0.0",
            item_count=32,
            ab_result=ab_result,
        )
        self.assertEqual(report.item_count, 32)
        self.assertEqual(report.round_count, 3)
        self.assertIn("A/B 评测完成", report.summary)

    def test_report_serialization(self):
        engine = ABEngine(seed=42)
        dataset = generate_synthetic_dataset(count=10)
        ab_result = engine.run_ab_test(dataset, rounds=2)
        report = EvalReport.from_ab_test(
            dataset_name="test",
            dataset_version="1.0",
            item_count=10,
            ab_result=ab_result,
        )
        j = report.to_json()
        parsed = json.loads(j)
        self.assertEqual(parsed["item_count"], 10)

    def test_recommendations(self):
        engine = ABEngine(seed=42)
        dataset = generate_synthetic_dataset(count=32)
        ab_result = engine.run_ab_test(dataset, rounds=3)
        report = EvalReport.from_ab_test(
            dataset_name="test",
            dataset_version="1.0",
            item_count=32,
            ab_result=ab_result,
        )
        # 优化策略应有建议
        self.assertIsInstance(report.recommendations, list)


if __name__ == "__main__":
    unittest.main()
