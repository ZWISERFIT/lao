"""T6 日常成本对账流程 — 单元测试（全部合成数据）。

P1-A T6 规格落地（153-T6-Design）：
    - 4 家 Provider 账单格式定义与校验
    - 对账引擎聚合与比对
    - 差异报告生成

运行方式：
    python -m unittest cost_reconciliation.test_cost_reconciliation -v
"""

from __future__ import annotations

import json
import unittest

from cost_reconciliation.billing_schema import (
    Provider,
    BillingFormat,
    BillingSchema,
    DEEPSEEK_SCHEMA,
    QWEN_SCHEMA,
    TOKEN_PLAN_SCHEMA,
    NOVAROUTEAI_SCHEMA,
    BILLING_SCHEMAS,
    get_billing_schema,
)
from cost_reconciliation.reconciler import (
    CostRecord,
    AggregatedCost,
    ReconciliationConfig,
    Reconciler,
)
from cost_reconciliation.discrepancy import (
    DiscrepancyItem,
    DiscrepancyReport,
)


# ── 合成数据 ─────────────────────────────────────────────────────────

SYNTH_EXPECTED = [
    CostRecord(ts="2026-09-01T10:00:00Z", provider="deepseek", model="deepseek-chat",
               cost_usd=0.01, tokens_in=100, tokens_out=50),
    CostRecord(ts="2026-09-01T11:00:00Z", provider="deepseek", model="deepseek-chat",
               cost_usd=0.02, tokens_in=200, tokens_out=100),
    CostRecord(ts="2026-09-01T12:00:00Z", provider="qwen", model="qwen-turbo",
               cost_usd=0.005, tokens_in=50, tokens_out=25),
]

SYNTH_ACTUAL_MATCH = [
    CostRecord(ts="2026-09-01T10:00:00Z", provider="deepseek", model="deepseek-chat",
               cost_usd=0.01, tokens_in=100, tokens_out=50),
    CostRecord(ts="2026-09-01T11:00:00Z", provider="deepseek", model="deepseek-chat",
               cost_usd=0.02, tokens_in=200, tokens_out=100),
    CostRecord(ts="2026-09-01T12:00:00Z", provider="qwen", model="qwen-turbo",
               cost_usd=0.005, tokens_in=50, tokens_out=25),
]

SYNTH_ACTUAL_MISMATCH = [
    CostRecord(ts="2026-09-01T10:00:00Z", provider="deepseek", model="deepseek-chat",
               cost_usd=0.015, tokens_in=100, tokens_out=50),  # 50% 差异
    CostRecord(ts="2026-09-01T11:00:00Z", provider="deepseek", model="deepseek-chat",
               cost_usd=0.02, tokens_in=200, tokens_out=100),
    # qwen 缺失
]


# ── 账单格式测试 ─────────────────────────────────────────────────────

class TestBillingSchema(unittest.TestCase):
    """4 家 Provider 账单格式定义。"""

    def test_four_schemas_defined(self):
        self.assertEqual(len(BILLING_SCHEMAS), 4)

    def test_deepseek_schema(self):
        schema = DEEPSEEK_SCHEMA
        self.assertEqual(schema.provider, "deepseek")
        self.assertEqual(schema.format, "csv")
        errors = schema.validate()
        self.assertEqual(errors, [])

    def test_qwen_schema(self):
        schema = QWEN_SCHEMA
        self.assertEqual(schema.provider, "qwen")
        self.assertTrue(any(m.transform == "cny_to_usd" for m in schema.field_mappings))

    def test_token_plan_schema(self):
        schema = TOKEN_PLAN_SCHEMA
        self.assertEqual(schema.provider, "token-plan")
        self.assertTrue(any(m.transform == "credits_to_usd" for m in schema.field_mappings))

    def test_novarouteai_schema(self):
        schema = NOVAROUTEAI_SCHEMA
        self.assertEqual(schema.provider, "novarouteai")
        self.assertEqual(schema.format, "json")

    def test_get_billing_schema(self):
        schema = get_billing_schema("deepseek")
        self.assertIsNotNone(schema)
        self.assertEqual(schema.provider, "deepseek")

    def test_get_unknown_schema(self):
        schema = get_billing_schema("unknown")
        self.assertIsNone(schema)

    def test_invalid_schema_empty_provider(self):
        schema = BillingSchema(provider="", format="csv", required_fields=["a"],
                               field_mappings=[])
        errors = schema.validate()
        self.assertTrue(any("provider" in e for e in errors))

    def test_schema_serialization(self):
        d = DEEPSEEK_SCHEMA.to_dict()
        self.assertEqual(d["provider"], "deepseek")
        self.assertIn("field_mappings", d)


class TestProviderEnum(unittest.TestCase):
    """Provider 枚举。"""

    def test_four_providers(self):
        self.assertEqual(len(Provider), 4)


class TestBillingFormat(unittest.TestCase):
    """账单格式枚举。"""

    def test_two_formats(self):
        self.assertEqual(len(BillingFormat), 2)


# ── 对账引擎测试 ─────────────────────────────────────────────────────

class TestReconciler(unittest.TestCase):
    """对账引擎聚合与比对。"""

    def test_aggregate_single_provider(self):
        reconciler = Reconciler()
        result = reconciler.aggregate(SYNTH_EXPECTED[:2])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].provider, "deepseek")
        self.assertAlmostEqual(result[0].total_cost_usd, 0.03)
        self.assertEqual(result[0].record_count, 2)

    def test_aggregate_multiple_providers(self):
        reconciler = Reconciler()
        result = reconciler.aggregate(SYNTH_EXPECTED)
        self.assertEqual(len(result), 2)  # deepseek + qwen

    def test_aggregate_empty(self):
        reconciler = Reconciler()
        result = reconciler.aggregate([])
        self.assertEqual(result, [])

    def test_reconcile_match(self):
        reconciler = Reconciler()
        result = reconciler.reconcile(SYNTH_EXPECTED, SYNTH_ACTUAL_MATCH)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["discrepancies"]), 0)

    def test_reconcile_mismatch(self):
        reconciler = Reconciler()
        result = reconciler.reconcile(SYNTH_EXPECTED, SYNTH_ACTUAL_MISMATCH)
        self.assertIn(result["status"], ["alert", "blocked"])
        self.assertGreater(len(result["discrepancies"]), 0)

    def test_reconcile_missing_actual(self):
        reconciler = Reconciler()
        result = reconciler.reconcile(SYNTH_EXPECTED, [])
        self.assertEqual(result["status"], "blocked")
        self.assertGreater(len(result["discrepancies"]), 0)


class TestReconciliationConfig(unittest.TestCase):
    """对账配置。"""

    def test_default_valid(self):
        cfg = ReconciliationConfig()
        self.assertEqual(cfg.validate(), [])

    def test_invalid_window(self):
        cfg = ReconciliationConfig(window_hours=0)
        errors = cfg.validate()
        self.assertTrue(len(errors) > 0)

    def test_invalid_threshold(self):
        cfg = ReconciliationConfig(alert_threshold_pct=10, block_threshold_pct=5)
        errors = cfg.validate()
        self.assertTrue(any("block_threshold" in e for e in errors))


# ── 差异报告测试 ─────────────────────────────────────────────────────

class TestDiscrepancyReport(unittest.TestCase):
    """差异报告生成。"""

    def test_from_reconciliation_result(self):
        reconciler = Reconciler()
        result = reconciler.reconcile(SYNTH_EXPECTED, SYNTH_ACTUAL_MISMATCH)
        report = DiscrepancyReport.from_reconciliation_result(result)
        self.assertIn(report.status, ["alert", "blocked"])
        self.assertIn("待对账", report.notes)

    def test_summary(self):
        report = DiscrepancyReport(
            expected_total=0.035,
            actual_total=0.035,
            expected_count=3,
            actual_count=3,
            status="ok",
        )
        summary = report.summary()
        self.assertIn("对账状态", summary)
        self.assertIn("ok", summary)

    def test_serialization(self):
        report = DiscrepancyReport(
            expected_total=0.035,
            actual_total=0.035,
            expected_count=3,
            actual_count=3,
        )
        j = report.to_json()
        parsed = json.loads(j)
        self.assertEqual(parsed["expected_total"], 0.035)


class TestDiscrepancyItem(unittest.TestCase):
    """差异条目。"""

    def test_to_dict(self):
        item = DiscrepancyItem(
            window="2026-09-01",
            provider="deepseek",
            discrepancy_type="mismatch",
            expected_cost=0.01,
            actual_cost=0.015,
            status="alert",
        )
        d = item.to_dict()
        self.assertEqual(d["provider"], "deepseek")
        self.assertEqual(d["status"], "alert")


if __name__ == "__main__":
    unittest.main()
