"""T5 Token 字段字典 — 单元测试（全部合成数据）。

P1-A T5 规格落地（145-T5-Design）：
    - TokenRecord 创建/校验/序列化
    - compute_hit_rate 命中率计算
    - ProviderStats 聚合统计
    - ProviderStatsRegistry 多 Provider 管理

运行方式：
    python -m unittest token_dictionary.test_token_dictionary -v
"""

from __future__ import annotations

import json
import unittest

from token_dictionary.record import (
    TokenRecord,
    TOKEN_RECORD_SCHEMA,
    VALID_PROVIDERS,
    compute_hit_rate,
)
from token_dictionary.provider_stats import (
    ProviderStats,
    ProviderStatsRegistry,
    MIN_SAMPLES,
)


# ── 合成数据常量 ─────────────────────────────────────────────────────

SYNTH_REQUEST_ID = "req-synth-001"
SYNTH_TASK_ID = "550e8400-e29b-41d4-a716-446655440000"


def _make_record(
    provider: str = "deepseek",
    input_tokens: int = 1000,
    output_tokens: int = 500,
    cache_hit: int = 800,
    cache_miss: int = 200,
    cost_yuan: float = 0.015,
) -> TokenRecord:
    """合成数据工厂：快速创建 TokenRecord。"""
    return TokenRecord.create(
        request_id=SYNTH_REQUEST_ID,
        provider=provider,
        model="deepseek-chat",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_hit_tokens=cache_hit,
        cache_miss_tokens=cache_miss,
        cost_yuan=cost_yuan,
        pricing_regime="official-csv",
        task_id=SYNTH_TASK_ID,
        source="synthetic",
    )


# ── TokenRecord 测试 ─────────────────────────────────────────────────

class TestTokenRecordCreation(unittest.TestCase):
    """TokenRecord 创建：合法 provider 通过。"""

    def test_all_valid_providers(self):
        for p in VALID_PROVIDERS:
            rec = TokenRecord.create(
                request_id="r1", provider=p, model="test-model"
            )
            self.assertEqual(rec.provider, p)
            errors = rec.validate()
            self.assertEqual(errors, [], f"{p} 校验失败: {errors}")

    def test_auto_total_tokens(self):
        rec = _make_record(input_tokens=1000, output_tokens=500)
        self.assertEqual(rec.total_tokens, 1500)

    def test_auto_hit_rate(self):
        rec = _make_record(cache_hit=800, cache_miss=200)
        self.assertAlmostEqual(rec.cache_hit_rate, 0.8)


class TestTokenRecordRejection(unittest.TestCase):
    """TokenRecord 创建：非法 provider 拒绝。"""

    def test_invalid_provider(self):
        with self.assertRaises(ValueError):
            TokenRecord.create(
                request_id="r1", provider="invalid-provider", model="m"
            )

    def test_negative_input_tokens(self):
        with self.assertRaises(ValueError):
            TokenRecord.create(
                request_id="r1", provider="deepseek", model="m",
                input_tokens=-1,
            )

    def test_negative_cache_hit(self):
        with self.assertRaises(ValueError):
            TokenRecord.create(
                request_id="r1", provider="deepseek", model="m",
                cache_hit_tokens=-1,
            )


class TestTokenRecordValidation(unittest.TestCase):
    """TokenRecord 校验：validate() 检测非法值。"""

    def test_empty_request_id(self):
        rec = _make_record()
        rec.request_id = ""
        errors = rec.validate()
        self.assertTrue(any("request_id" in e for e in errors))

    def test_wrong_total_tokens(self):
        rec = _make_record()
        rec.total_tokens = 9999  # 故意写错
        errors = rec.validate()
        self.assertTrue(any("total_tokens" in e for e in errors))

    def test_hit_rate_out_of_range(self):
        rec = _make_record()
        rec.cache_hit_rate = 1.5
        errors = rec.validate()
        self.assertTrue(any("cache_hit_rate" in e for e in errors))


class TestTokenRecordSerialization(unittest.TestCase):
    """TokenRecord 序列化/反序列化。"""

    def test_round_trip(self):
        rec = _make_record()
        json_str = rec.to_json()
        data = json.loads(json_str)
        restored = TokenRecord.from_dict(data)
        self.assertEqual(rec.request_id, restored.request_id)
        self.assertEqual(rec.provider, restored.provider)
        self.assertEqual(rec.total_tokens, restored.total_tokens)
        self.assertAlmostEqual(rec.cache_hit_rate, restored.cache_hit_rate)
        errors = restored.validate()
        self.assertEqual(errors, [])


# ── compute_hit_rate 测试 ────────────────────────────────────────────

class TestComputeHitRate(unittest.TestCase):
    """命中率计算函数。"""

    def test_normal(self):
        self.assertAlmostEqual(compute_hit_rate(800, 200), 0.8)

    def test_zero_denominator(self):
        self.assertIsNone(compute_hit_rate(0, 0))

    def test_all_hit(self):
        self.assertAlmostEqual(compute_hit_rate(1000, 0), 1.0)

    def test_all_miss(self):
        self.assertAlmostEqual(compute_hit_rate(0, 1000), 0.0)


# ── ProviderStats 测试 ───────────────────────────────────────────────

class TestProviderStats(unittest.TestCase):
    """ProviderStats 聚合统计。"""

    def test_initial_empty(self):
        stats = ProviderStats(provider="deepseek")
        self.assertEqual(stats.sample_count, 0)
        self.assertIsNone(stats.hit_rate())

    def test_add_records(self):
        stats = ProviderStats(provider="deepseek")
        for _ in range(5):
            stats.add_record(_make_record())
        self.assertEqual(stats.sample_count, 5)
        self.assertIsNone(stats.hit_rate())  # 样本不足 MIN_SAMPLES

    def test_hit_rate_with_min_samples(self):
        stats = ProviderStats(provider="deepseek")
        for _ in range(MIN_SAMPLES):
            stats.add_record(_make_record(cache_hit=800, cache_miss=200))
        self.assertEqual(stats.sample_count, MIN_SAMPLES)
        self.assertAlmostEqual(stats.hit_rate(), 0.8)

    def test_provider_mismatch_rejected(self):
        stats = ProviderStats(provider="deepseek")
        rec = _make_record(provider="qwen")
        with self.assertRaises(ValueError):
            stats.add_record(rec)

    def test_invalid_provider_rejected(self):
        with self.assertRaises(ValueError):
            ProviderStats(provider="invalid")

    def test_summary(self):
        stats = ProviderStats(provider="deepseek")
        for _ in range(MIN_SAMPLES):
            stats.add_record(_make_record(
                input_tokens=1000, output_tokens=500,
                cache_hit=800, cache_miss=200, cost_yuan=0.01,
            ))
        s = stats.summary()
        self.assertEqual(s["provider"], "deepseek")
        self.assertEqual(s["sample_count"], MIN_SAMPLES)
        self.assertTrue(s["min_samples_met"])
        self.assertAlmostEqual(s["hit_rate"], 0.8)
        self.assertEqual(s["total_input_tokens"], 1000 * MIN_SAMPLES)
        self.assertEqual(s["total_output_tokens"], 500 * MIN_SAMPLES)


# ── ProviderStatsRegistry 测试 ───────────────────────────────────────

class TestProviderStatsRegistry(unittest.TestCase):
    """ProviderStatsRegistry 多 Provider 管理。"""

    def test_all_providers_initialized(self):
        reg = ProviderStatsRegistry()
        for p in VALID_PROVIDERS:
            stats = reg.get_stats(p)
            self.assertIsNotNone(stats)
            self.assertEqual(stats.sample_count, 0)

    def test_record_and_query(self):
        reg = ProviderStatsRegistry()
        for _ in range(MIN_SAMPLES):
            reg.record(_make_record(provider="deepseek", cache_hit=600, cache_miss=400))
        rate = reg.get_hit_rate("deepseek")
        self.assertAlmostEqual(rate, 0.6)

    def test_unknown_provider_ignored(self):
        reg = ProviderStatsRegistry()
        rec = TokenRecord(
            request_id="r1", provider="unknown", model="m",
            input_tokens=100, output_tokens=50,
        )
        reg.record(rec)  # 不应抛异常

    def test_all_summaries(self):
        reg = ProviderStatsRegistry()
        summaries = reg.all_summaries()
        self.assertEqual(len(summaries), len(VALID_PROVIDERS))
        for p in VALID_PROVIDERS:
            self.assertIn(p, summaries)

    def test_multi_provider_isolation(self):
        reg = ProviderStatsRegistry()
        for _ in range(MIN_SAMPLES):
            reg.record(_make_record(provider="deepseek", cache_hit=800, cache_miss=200))
            reg.record(_make_record(provider="qwen", cache_hit=200, cache_miss=800))
        self.assertAlmostEqual(reg.get_hit_rate("deepseek"), 0.8)
        self.assertAlmostEqual(reg.get_hit_rate("qwen"), 0.2)
        # token-plan 和 novarouteai 无样本
        self.assertIsNone(reg.get_hit_rate("token-plan"))
        self.assertIsNone(reg.get_hit_rate("novarouteai"))


# ── Provider 枚举测试 ────────────────────────────────────────────────

class TestProviderEnum(unittest.TestCase):
    """Provider 枚举校验。"""

    def test_four_providers(self):
        self.assertEqual(len(VALID_PROVIDERS), 4)
        self.assertIn("deepseek", VALID_PROVIDERS)
        self.assertIn("qwen", VALID_PROVIDERS)
        self.assertIn("token-plan", VALID_PROVIDERS)
        self.assertIn("novarouteai", VALID_PROVIDERS)

    def test_schema_enum_matches(self):
        schema_providers = set(
            TOKEN_RECORD_SCHEMA["properties"]["provider"]["enum"]
        )
        self.assertEqual(schema_providers, VALID_PROVIDERS)


if __name__ == "__main__":
    unittest.main()
