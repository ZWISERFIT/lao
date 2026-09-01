# P0 LAO成本防线: 规格书 _rev2 (SHA-256 4d6bc3aa…17c4) 验收件
"""S1 烧钱哨兵回归测试 (F-1 纯成本类 / F-4 去重与升级 / S1-6 UTC / S1-7 活性)。

复演纪律 (七·复演样本集): 一律旁路注入测试数据流, 不在真实路由/真实账本上制造告警。
运行: python3 -m pytest lao/effect_anchored/cost_defense/tests/test_s1_sentinel.py -q
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

from lao.effect_anchored.cost_defense.model import CostEvent, SwitchEvent  # noqa: E402
from lao.effect_anchored.cost_defense.sentinel import (  # noqa: E402
    CostSentinel,
    resolve_daily_budget,
)

UTC = timezone.utc
NOW = datetime(2026, 8, 31, 12, 30, tzinfo=UTC)


def cost_event(cost: float, ts: datetime, task: str = "medium", agent: str = "shuyu",
               task_id: str = "") -> CostEvent:
    """构造一笔注入用成本事件(非真实账本)。"""
    return CostEvent(
        ts=ts, provider="token-plan", model="qwen3.7-plus", task=task, task_id=task_id,
        agent=agent, cost_usd=cost, synthetic=False, source="injected",
    )


def switch_event(ts: datetime, to_provider: str = "deepseek") -> SwitchEvent:
    """构造一笔注入用切换事件(非真实审计账本)。"""
    return SwitchEvent(
        ts=ts, from_provider="token-plan", to_provider=to_provider,
        to_model="deepseek-chat", reason="agent_binding", triggered_by="auto",
        source="injected",
    )


class TestBudgetResolution(unittest.TestCase):
    """S1-3: 军团全局日预算 7.0 USD 口径。"""

    def test_default_is_seven(self):
        self.assertEqual(resolve_daily_budget({}), 7.0)

    def test_env_override(self):
        self.assertEqual(resolve_daily_budget({"LAO_DAILY_BUDGET": "7.0"}), 7.0)

    def test_illegal_value_falls_back(self):
        self.assertEqual(resolve_daily_budget({"LAO_DAILY_BUDGET": "abc"}), 7.0)
        self.assertEqual(resolve_daily_budget({"LAO_DAILY_BUDGET": "0"}), 7.0)


class TestF1CostLevels(unittest.TestCase):
    """F-1 纯成本类告警: 70%/90%/100% 逐级触发, axis=cost。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sentinel = CostSentinel(self.tmp.name, daily_budget_usd=7.0)

    def tearDown(self):
        self.tmp.cleanup()

    def _eval(self, day_total: float, now: datetime = NOW):
        # 均摊到当日多个小时, 避免小时轴先行触发, 以隔离验证日累计轴
        events = [
            cost_event(day_total / 7.0, now.replace(hour=h, minute=5))
            for h in range(5, 12)
        ]
        return self.sentinel.evaluate(events, [], now)

    def test_l1_at_seventy_percent(self):
        alerts = self._eval(7.0 * 0.70)
        cost_alerts = [a for a in alerts if a.axis == "cost"]
        self.assertTrue(cost_alerts)
        self.assertEqual(cost_alerts[0].level, "L1")
        self.assertEqual(cost_alerts[0].budget_usd, 7.0)

    def test_l2_at_ninety_percent(self):
        alerts = self._eval(7.0 * 0.90)
        self.assertEqual([a.level for a in alerts if a.axis == "cost"][0], "L2")

    def test_l3_at_full_budget(self):
        alerts = self._eval(7.0)
        self.assertEqual([a.level for a in alerts if a.axis == "cost"][0], "L3")

    def test_below_threshold_is_silent(self):
        self.assertEqual([a for a in self._eval(7.0 * 0.5) if a.axis == "cost"], [])

    def test_agent_breakdown_present(self):
        """S1-3: 告警明细必须含 agent 维度消耗分解。"""
        events = [
            cost_event(3.0, NOW.replace(hour=6), agent="shuyu"),
            cost_event(3.0, NOW.replace(hour=7), agent="nova"),
        ]
        alerts = self.sentinel.evaluate(events, [], NOW)
        breakdown = [a for a in alerts if a.axis == "cost"][0].agent_cost_breakdown
        self.assertEqual(set(breakdown), {"shuyu", "nova"})

    def test_synthetic_records_excluded(self):
        """R-02: 合成/测试记录不计入成本, 不产生误报。"""
        events = [
            CostEvent(ts=NOW.replace(hour=h), task="test", cost_usd=1.0,
                      synthetic=True, source="injected")
            for h in range(5, 12)
        ]
        self.assertEqual(self.sentinel.evaluate(events, [], NOW), [])


class TestSingleTaskAxis(unittest.TestCase):
    """S1-3 单任务轴: 仅对显式 task_id 可归因的笔判定。

    归因纪律: routing_cost_log 的 `task` 是任务类型(medium/code/test),
    不得冒充任务 ID, 否则整类费用会被归成一笔而虚报。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sentinel = CostSentinel(self.tmp.name, daily_budget_usd=7.0)

    def tearDown(self):
        self.tmp.cleanup()

    def test_task_axis_fires_with_task_id(self):
        events = [cost_event(0.8, NOW.replace(hour=6), task_id="t-0001")]
        alerts = self.sentinel.evaluate(events, [], NOW)
        task_alerts = [a for a in alerts if a.trigger_rule.startswith("task_cost")]
        self.assertTrue(task_alerts)
        self.assertEqual(task_alerts[0].task_id, "t-0001")

    def test_task_type_alone_does_not_fire_task_axis(self):
        events = [cost_event(0.5, NOW.replace(hour=h, minute=5)) for h in range(5, 12)]
        alerts = self.sentinel.evaluate(events, [], NOW)
        self.assertEqual([a for a in alerts if a.trigger_rule.startswith("task_cost")], [])


class TestSwitchAnomalyAxis(unittest.TestCase):
    """S1-3 切换异常轴: 单小时切入同一非白名单 provider ≥20 笔。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sentinel = CostSentinel(self.tmp.name, daily_budget_usd=7.0)

    def tearDown(self):
        self.tmp.cleanup()

    def test_twenty_switches_trigger(self):
        events = [switch_event(NOW.replace(minute=i)) for i in range(20)]
        alerts = self.sentinel.evaluate([], events, NOW, allowed_providers=["token-plan"])
        switch_alerts = [a for a in alerts if a.axis == "switch"]
        self.assertTrue(switch_alerts)
        self.assertEqual(switch_alerts[0].provider, "deepseek")

    def test_nineteen_switches_silent(self):
        events = [switch_event(NOW.replace(minute=i)) for i in range(19)]
        alerts = self.sentinel.evaluate([], events, NOW, allowed_providers=["token-plan"])
        self.assertEqual([a for a in alerts if a.axis == "switch"], [])

    def test_whitelisted_provider_not_flagged(self):
        events = [switch_event(NOW.replace(minute=i), "token-plan") for i in range(30)]
        alerts = self.sentinel.evaluate([], events, NOW, allowed_providers=["token-plan"])
        self.assertEqual([a for a in alerts if a.axis == "switch"], [])


class TestF4DedupAndEscalation(unittest.TestCase):
    """F-4 去重与升级: 同键 4h 抑制、升级放行、恢复通知各 1 次。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _sentinel(self):
        return CostSentinel(self.tmp.name, daily_budget_usd=7.0)

    def _day_events(self, total: float, now: datetime):
        return [cost_event(total / 7.0, now.replace(hour=h, minute=5)) for h in range(5, 12)]

    def test_same_key_suppressed_within_window(self):
        s = self._sentinel()
        first = s.evaluate(self._day_events(4.9, NOW), [], NOW)
        self.assertTrue([a for a in first if a.axis == "cost"])
        again = self._sentinel().evaluate(
            self._day_events(4.9, NOW), [], NOW + timedelta(hours=1)
        )
        self.assertEqual([a for a in again if a.axis == "cost" and a.status == "fired"], [])

    def test_escalation_bypasses_dedup(self):
        s = self._sentinel()
        s.evaluate(self._day_events(4.9, NOW), [], NOW)
        escalated = self._sentinel().evaluate(
            self._day_events(7.0, NOW), [], NOW + timedelta(minutes=30)
        )
        self.assertEqual([a.level for a in escalated if a.axis == "cost"][0], "L3")

    def test_same_key_released_after_window(self):
        s = self._sentinel()
        s.evaluate(self._day_events(4.9, NOW), [], NOW)
        later = self._sentinel().evaluate(
            self._day_events(4.9, NOW), [], NOW + timedelta(hours=5)
        )
        self.assertTrue([a for a in later if a.axis == "cost" and a.status == "fired"])

    def test_recovery_notice_once(self):
        s = self._sentinel()
        s.evaluate(self._day_events(4.9, NOW), [], NOW)
        recovered = self._sentinel().evaluate([], [], NOW + timedelta(hours=5))
        self.assertEqual([a.status for a in recovered], ["recovered"])
        again = self._sentinel().evaluate([], [], NOW + timedelta(hours=6))
        self.assertEqual([a for a in again if a.status == "recovered"], [])


class TestUtcWindowing(unittest.TestCase):
    """S1-6: 归窗一律 UTC, 不做本地时区换算。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sentinel = CostSentinel(self.tmp.name, daily_budget_usd=7.0)

    def tearDown(self):
        self.tmp.cleanup()

    def test_event_in_previous_utc_day_not_counted(self):
        prev = [cost_event(7.0, datetime(2026, 8, 30, 23, 30, tzinfo=UTC))]
        self.assertEqual(self.sentinel.evaluate(prev, [], NOW), [])

    def test_alert_ts_is_utc(self):
        alerts = self.sentinel.evaluate(
            [cost_event(7.0, NOW.replace(hour=6))], [], NOW
        )
        self.assertTrue(alerts[0].ts.endswith("+00:00"))


class TestS7DataLiveness(unittest.TestCase):
    """S1-7: 零成本≠安全 — 断流且网关活跃时须报 DATA_GAP。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sentinel = CostSentinel(self.tmp.name, daily_budget_usd=7.0)

    def tearDown(self):
        self.tmp.cleanup()

    def test_gap_reported_when_session_active(self):
        alerts = self.sentinel.evaluate([], [], NOW, session_active=True)
        self.assertEqual([a.trigger_rule for a in alerts][0],
                         "no_event_for>=2h_with_active_session")

    def test_no_gap_when_session_idle(self):
        self.assertEqual(self.sentinel.evaluate([], [], NOW, session_active=False), [])

    def test_no_gap_when_recent_event_exists(self):
        recent = [cost_event(0.01, NOW - timedelta(minutes=30))]
        alerts = self.sentinel.evaluate(recent, [], NOW, session_active=True)
        self.assertEqual([a for a in alerts if a.axis == "behavior"], [])


class TestAlertPersistence(unittest.TestCase):
    """S1-4/S1-5: 告警落盘 share/state/lao-cost-sentinel-YYYYMMDD.jsonl。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sentinel = CostSentinel(self.tmp.name, daily_budget_usd=7.0)

    def tearDown(self):
        self.tmp.cleanup()

    def test_write_alerts_creates_dated_log(self):
        alerts = self.sentinel.evaluate([cost_event(7.0, NOW.replace(hour=6))], [], NOW)
        path = self.sentinel.write_alerts(alerts, NOW)
        self.assertTrue(path.endswith("lao-cost-sentinel-20260831.jsonl"))
        self.assertTrue(os.path.isfile(path))

    def test_record_contains_spec_fields(self):
        alerts = self.sentinel.evaluate([cost_event(7.0, NOW.replace(hour=6))], [], NOW)
        record = alerts[0].to_record()
        for key in (
            "alert_id", "ts", "level", "axis", "trigger_rule", "window",
            "agent_cost_breakdown", "cost_basis", "budget_usd", "dedup_key",
            "status", "escalation_level", "escalated_to",
        ):
            self.assertIn(key, record)
        self.assertEqual(record["cost_basis"], "unreconciled")


if __name__ == "__main__":
    unittest.main(verbosity=2)
