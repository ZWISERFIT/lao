# P0 LAO成本防线: 规格书 _rev2 (SHA-256 4d6bc3aa…17c4) 验收件
"""S2 当日指令白名单 (N1-b) 回归测试。

覆盖验收门:
    - S2-1 数据结构: JSON 解析、日粒度过期(次日 00:00 UTC 兜底)。
    - S2-1 默认安全态: 文件缺失/过期/损坏/空列表 → 一切非文件内 provider 切换发 🟠。
    - S2-2 校验点: denied 命中 → L3 axis=both; 未列入 allowed → L2。
    - R-01 白名单第1笔即告警: audit_stream 的 first_hit_index == 1。
    - F-4 去重: 同 provider 同小时抑制, 跨小时放行。

一律注入式测试数据流, 不读写真实账本。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime

from ..model import COST_BASIS_UNRECONCILED, UTC, SwitchEvent
from ..sentinel import DedupStore
from ..whitelist import (
    STATUS_EMPTY,
    STATUS_EXPIRED,
    STATUS_MALFORMED,
    STATUS_MISSING,
    STATUS_NOT_YET_EFFECTIVE,
    STATUS_VALID,
    DailyWhitelist,
    WhitelistAuditor,
    load_whitelist,
    whitelist_path,
)

DAY = "2026-08-30"


def _ts(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 30, hour, minute, tzinfo=UTC)


def _switch(hour: int, to_provider: str, minute: int = 0, reason: str = "quota_degrade_flash") -> SwitchEvent:
    return SwitchEvent(
        ts=_ts(hour, minute),
        from_provider="token-plan",
        from_model="qwen3-coder-plus",
        to_provider=to_provider,
        to_model="deepseek-chat",
        reason=reason,
        triggered_by="model_router",
        source="injected",
    )


def _write_whitelist(state_dir: str, day: str = DAY, **overrides) -> str:
    payload = {
        "date": day,
        "signed_by": "统筹席",
        "allowed_providers": ["token-plan"],
        "denied_providers": ["deepseek"],
        "effective_from": f"{day}T00:00:00+00:00",
        "expires_at": "2026-08-31T00:00:00+00:00",
        "notes": "测试用注入白名单",
    }
    payload.update(overrides)
    path = whitelist_path(state_dir, day)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    return path


class _TempStateCase(unittest.TestCase):
    def setUp(self) -> None:
        self.state_dir = tempfile.mkdtemp(prefix="lao-s2-")

    def tearDown(self) -> None:
        shutil.rmtree(self.state_dir, ignore_errors=True)


class TestWhitelistLoading(_TempStateCase):
    """S2-1 白名单加载与日粒度过期。"""

    def test_valid_file(self) -> None:
        _write_whitelist(self.state_dir)
        wl, status = load_whitelist(self.state_dir, _ts(9), day=DAY)
        self.assertEqual(status, STATUS_VALID)
        self.assertIsNotNone(wl)
        self.assertEqual(wl.signed_by, "统筹席")
        self.assertEqual(wl.verdict("deepseek"), "denied")
        self.assertEqual(wl.verdict("token-plan"), "allowed")
        self.assertEqual(wl.verdict("openai"), "unlisted")

    def test_missing_file(self) -> None:
        wl, status = load_whitelist(self.state_dir, _ts(9), day=DAY)
        self.assertEqual(status, STATUS_MISSING)
        self.assertIsNone(wl)

    def test_malformed_file(self) -> None:
        path = whitelist_path(self.state_dir, DAY)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("allowed: token-plan\n")  # 自由文本, 非 JSON
        wl, status = load_whitelist(self.state_dir, _ts(9), day=DAY)
        self.assertEqual(status, STATUS_MALFORMED)
        self.assertIsNone(wl)

    def test_missing_list_fields_is_malformed(self) -> None:
        _write_whitelist(self.state_dir, allowed_providers="token-plan")
        _, status = load_whitelist(self.state_dir, _ts(9), day=DAY)
        self.assertEqual(status, STATUS_MALFORMED)

    def test_expired_next_day(self) -> None:
        _write_whitelist(self.state_dir)
        # 次日 00:00 UTC 即过期(日粒度自动过期)
        _, status = load_whitelist(
            self.state_dir, datetime(2026, 8, 31, 0, 0, tzinfo=UTC), day=DAY
        )
        self.assertEqual(status, STATUS_EXPIRED)

    def test_expires_at_defaults_to_next_day_utc(self) -> None:
        payload_path = _write_whitelist(self.state_dir)
        with open(payload_path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        payload.pop("expires_at")
        with open(payload_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        wl, status = load_whitelist(self.state_dir, _ts(23, 59), day=DAY)
        self.assertEqual(status, STATUS_VALID)
        self.assertEqual(wl.expires_at, datetime(2026, 8, 31, 0, 0, tzinfo=UTC))

    def test_not_yet_effective(self) -> None:
        _write_whitelist(self.state_dir, effective_from=f"{DAY}T10:00:00+00:00")
        _, status = load_whitelist(self.state_dir, _ts(9), day=DAY)
        self.assertEqual(status, STATUS_NOT_YET_EFFECTIVE)

    def test_empty_lists_fall_back_to_safe_state(self) -> None:
        _write_whitelist(self.state_dir, allowed_providers=[], denied_providers=[])
        _, status = load_whitelist(self.state_dir, _ts(9), day=DAY)
        self.assertEqual(status, STATUS_EMPTY)

    def test_day_defaults_to_utc_window_of_now(self) -> None:
        _write_whitelist(self.state_dir)
        _, status = load_whitelist(self.state_dir, _ts(9))
        self.assertEqual(status, STATUS_VALID)


class TestS2Verdicts(unittest.TestCase):
    """S2-2 校验点判定与告警形态。"""

    def setUp(self) -> None:
        self.wl = DailyWhitelist(
            date=DAY,
            allowed_providers=["token-plan"],
            denied_providers=["deepseek"],
            signed_by="统筹席",
            effective_from=_ts(0),
            expires_at=datetime(2026, 8, 31, tzinfo=UTC),
            source_path="/injected/whitelist.json",
        )
        self.auditor = WhitelistAuditor(daily_budget_usd=7.0)

    def test_denied_provider_is_l3_both(self) -> None:
        alert = self.auditor.evaluate_event(_switch(9, "deepseek"), self.wl, STATUS_VALID)
        self.assertIsNotNone(alert)
        self.assertEqual(alert.level, "L3")
        self.assertEqual(alert.axis, "both")
        self.assertEqual(alert.trigger_rule, "to_provider_in_denied_providers")
        self.assertEqual(alert.provider, "deepseek")

    def test_allowed_provider_is_silent(self) -> None:
        self.assertIsNone(
            self.auditor.evaluate_event(_switch(9, "token-plan"), self.wl, STATUS_VALID)
        )

    def test_unlisted_provider_is_l2(self) -> None:
        alert = self.auditor.evaluate_event(_switch(9, "openai"), self.wl, STATUS_VALID)
        self.assertIsNotNone(alert)
        self.assertEqual(alert.level, "L2")
        self.assertEqual(alert.trigger_rule, "to_provider_not_in_allowed_providers")

    def test_case_insensitive_match(self) -> None:
        alert = self.auditor.evaluate_event(_switch(9, "DeepSeek"), self.wl, STATUS_VALID)
        self.assertIsNotNone(alert)
        self.assertEqual(alert.trigger_rule, "to_provider_in_denied_providers")

    def test_empty_whitelist_never_silently_passes(self) -> None:
        empty = DailyWhitelist(date=DAY, effective_from=_ts(0), expires_at=datetime(2026, 8, 31, tzinfo=UTC))
        alert = self.auditor.evaluate_event(_switch(9, "deepseek"), empty, STATUS_EMPTY)
        self.assertIsNotNone(alert)
        self.assertEqual(alert.level, "L2")
        self.assertEqual(alert.trigger_rule, "whitelist_empty")

    def test_alert_record_shape(self) -> None:
        alert = self.auditor.evaluate_event(_switch(9, "deepseek"), self.wl, STATUS_VALID)
        rec = alert.to_record()
        for key in (
            "alert_id",
            "ts",
            "level",
            "axis",
            "trigger_rule",
            "window",
            "provider",
            "cost_basis",
            "budget_usd",
            "dedup_key",
            "escalation_level",
            "escalated_to",
        ):
            self.assertIn(key, rec)
        self.assertEqual(rec["cost_basis"], COST_BASIS_UNRECONCILED)
        self.assertEqual(rec["budget_usd"], 7.0)
        self.assertEqual(rec["window"], "2026-08-30T09")
        self.assertEqual(rec["sample_details"][0]["whitelist_source"], "/injected/whitelist.json")


class TestFallbackSafeState(unittest.TestCase):
    """S2-1 默认安全态: 宁紧勿松。"""

    def setUp(self) -> None:
        self.auditor = WhitelistAuditor(daily_budget_usd=7.0)

    def test_missing_whitelist_alerts_every_switch(self) -> None:
        alert = self.auditor.evaluate_event(_switch(9, "token-plan"), None, STATUS_MISSING)
        self.assertIsNotNone(alert)
        self.assertEqual(alert.level, "L2")
        self.assertEqual(alert.trigger_rule, "whitelist_missing")

    def test_expired_whitelist_does_not_authorize(self) -> None:
        stale = DailyWhitelist(
            date="2026-08-29",
            allowed_providers=["deepseek"],
            effective_from=datetime(2026, 8, 29, tzinfo=UTC),
            expires_at=_ts(0),
        )
        alert = self.auditor.evaluate_event(_switch(9, "deepseek"), stale, STATUS_EXPIRED)
        self.assertIsNotNone(alert)
        self.assertEqual(alert.trigger_rule, "whitelist_expired")

    def test_malformed_whitelist_falls_back(self) -> None:
        alert = self.auditor.evaluate_event(_switch(9, "openai"), None, STATUS_MALFORMED)
        self.assertIsNotNone(alert)
        self.assertEqual(alert.trigger_rule, "whitelist_malformed")


class TestFirstEventTriggers(unittest.TestCase):
    """R-01 白名单轴验收: 第1笔越界即告警。"""

    def setUp(self) -> None:
        self.wl = DailyWhitelist(
            date=DAY,
            allowed_providers=["token-plan"],
            denied_providers=["deepseek"],
            effective_from=_ts(0),
            expires_at=datetime(2026, 8, 31, tzinfo=UTC),
        )
        self.auditor = WhitelistAuditor(daily_budget_usd=7.0)

    def test_first_offending_event_fires(self) -> None:
        events = [_switch(8, "deepseek", minute=m) for m in range(5)]
        result = self.auditor.audit_stream(events, self.wl, STATUS_VALID)
        self.assertEqual(result["first_hit_index"], 1)
        self.assertEqual(len(result["alerts"]), 5)

    def test_index_counts_only_offending_position(self) -> None:
        events = [_switch(8, "token-plan", minute=0), _switch(8, "deepseek", minute=1)]
        result = self.auditor.audit_stream(events, self.wl, STATUS_VALID)
        self.assertEqual(result["first_hit_index"], 2)
        self.assertEqual(result["scanned"], 2)

    def test_stream_is_time_ordered(self) -> None:
        events = [_switch(9, "deepseek", minute=0), _switch(8, "token-plan", minute=0)]
        result = self.auditor.audit_stream(events, self.wl, STATUS_VALID)
        # 乱序输入按 ts 排序后, 越界笔位于第2位
        self.assertEqual(result["first_hit_index"], 2)

    def test_no_offense_no_alert(self) -> None:
        events = [_switch(h, "token-plan") for h in (8, 9, 10)]
        result = self.auditor.audit_stream(events, self.wl, STATUS_VALID)
        self.assertIsNone(result["first_hit_index"])
        self.assertEqual(result["alerts"], [])


class TestWhitelistDedup(_TempStateCase):
    """F-4 去重: 同 provider 同小时抑制, 跨小时放行。"""

    def setUp(self) -> None:
        super().setUp()
        self.wl = DailyWhitelist(
            date=DAY,
            allowed_providers=["token-plan"],
            denied_providers=["deepseek"],
            effective_from=_ts(0),
            expires_at=datetime(2026, 8, 31, tzinfo=UTC),
        )

    def test_same_hour_suppressed(self) -> None:
        auditor = WhitelistAuditor(dedup_store=DedupStore(self.state_dir))
        events = [_switch(8, "deepseek", minute=m) for m in (0, 10, 20)]
        result = auditor.audit_stream(events, self.wl, STATUS_VALID)
        self.assertEqual(result["first_hit_index"], 1)
        self.assertEqual(len(result["alerts"]), 1)

    def test_distinct_providers_not_merged(self) -> None:
        auditor = WhitelistAuditor(dedup_store=DedupStore(self.state_dir))
        events = [_switch(8, "deepseek"), _switch(8, "openai", minute=5)]
        result = auditor.audit_stream(events, self.wl, STATUS_VALID)
        self.assertEqual(len(result["alerts"]), 2)

    def test_dedup_state_is_persisted(self) -> None:
        store = DedupStore(self.state_dir)
        auditor = WhitelistAuditor(dedup_store=store)
        auditor.audit_stream([_switch(8, "deepseek")], self.wl, STATUS_VALID)
        self.assertTrue(
            os.path.isfile(os.path.join(self.state_dir, "lao-cost-sentinel-dedup.json"))
        )
        # 换新实例读同一状态目录, 4h 窗内仍抑制
        again = WhitelistAuditor(dedup_store=DedupStore(self.state_dir))
        result = again.audit_stream([_switch(8, "deepseek", minute=30)], self.wl, STATUS_VALID)
        self.assertEqual(result["alerts"], [])


class TestBypassDiscipline(unittest.TestCase):
    """S2-3 旁路纪律: 审计器只读, 不产生白名单文件。"""

    def test_auditor_never_writes_whitelist(self) -> None:
        state_dir = tempfile.mkdtemp(prefix="lao-s2-ro-")
        try:
            before = set(os.listdir(state_dir))
            auditor = WhitelistAuditor(daily_budget_usd=7.0)
            auditor.audit_stream([_switch(8, "deepseek")], None, STATUS_MISSING)
            self.assertEqual(set(os.listdir(state_dir)), before)
        finally:
            shutil.rmtree(state_dir, ignore_errors=True)

    def test_load_does_not_create_file(self) -> None:
        state_dir = tempfile.mkdtemp(prefix="lao-s2-ro2-")
        try:
            load_whitelist(state_dir, _ts(9), day=DAY)
            self.assertFalse(os.path.exists(whitelist_path(state_dir, DAY)))
        finally:
            shutil.rmtree(state_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
