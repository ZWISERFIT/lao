# P0 LAO成本防线: 规格书 _rev2 (SHA-256 4d6bc3aa…17c4) 验收件
"""S3 Ethan 行为建模双路由 (N1-c) 回归测试。

覆盖验收门:
    - S3-1 分类: cost→Nova / behavior→Ethan / both→双送 / switch→Ethan / 未知 axis 兜底双送。
    - S3-2 送达: 原子写(无残留临时文件)、delivered_to 落盘、ACK 回执闭环、保留期只报不删。
    - S3-3 治理阶梯: 五级字段、基建类转 Tristan、未闭环上抬、L3 久悬到创始人、escalation_path 留痕。
    - S3-4 Nova 接账: cost_basis != official_ledger 时标记须先官方对账。
    - F-1/F-2/F-3 三类出箱实证(注入式)。

一律注入式数据流, 不触碰真实账本、不写生产 share/inbox。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta

from ..model import COST_BASIS_LOCAL, COST_BASIS_OFFICIAL, UTC, Alert
from ..router import (
    ACK_TIMEOUT_HOURS,
    E1_UNIT_AGENTS,
    E2_UNIT_LEAD,
    E3_INFRA_OPS,
    E4_THINK_TANK,
    E5_FOUNDER,
    OUTBOX_RETENTION_DAYS,
    RECIPIENT_ETHAN,
    RECIPIENT_NOVA,
    AlertOutbox,
    classify,
    decide_escalation,
    needs_reconciliation,
    outbox_root,
)

NOW = datetime(2026, 8, 30, 15, 0, tzinfo=UTC)


def _alert(
    alert_id: str = "a1",
    level: str = "L1",
    axis: str = "cost",
    rule: str = "day_cost>=70%budget",
    cost_basis: str = COST_BASIS_LOCAL,
    cost_usd: float = 4.9,
) -> Alert:
    return Alert(
        alert_id=alert_id,
        ts="2026-08-30T15:00:00+00:00",
        level=level,
        axis=axis,
        trigger_rule=rule,
        window="2026-08-30",
        dedup_key=f"({rule},2026-08-30)",
        provider="deepseek",
        cost_usd=cost_usd,
        cost_basis=cost_basis,
        budget_usd=7.0,
    )


class TestS31Classification(unittest.TestCase):
    """S3-1 分类规则(决定送谁)。"""

    def test_cost_goes_to_nova(self) -> None:
        self.assertEqual(classify(_alert(axis="cost")), [RECIPIENT_NOVA])

    def test_behavior_goes_to_ethan(self) -> None:
        self.assertEqual(classify(_alert(axis="behavior")), [RECIPIENT_ETHAN])

    def test_both_goes_to_nova_and_ethan(self) -> None:
        self.assertEqual(classify(_alert(axis="both")), [RECIPIENT_NOVA, RECIPIENT_ETHAN])

    def test_switch_axis_is_behavior_class(self) -> None:
        self.assertEqual(classify(_alert(axis="switch")), [RECIPIENT_ETHAN])

    def test_unknown_axis_falls_back_to_both(self) -> None:
        self.assertEqual(
            classify(_alert(axis="somethingelse")), [RECIPIENT_NOVA, RECIPIENT_ETHAN]
        )


class TestS33EscalationLadder(unittest.TestCase):
    """S3-3 五级治理阶梯定级。"""

    def test_l1_stays_in_unit(self) -> None:
        level, owners = decide_escalation(_alert(level="L1"))
        self.assertEqual(level, E1_UNIT_AGENTS)
        self.assertEqual(owners, [RECIPIENT_NOVA])

    def test_l2_goes_to_stella(self) -> None:
        level, owners = decide_escalation(_alert(level="L2"))
        self.assertEqual(level, E2_UNIT_LEAD)
        self.assertEqual(owners, ["stella"])

    def test_infra_rule_goes_to_tristan(self) -> None:
        level, owners = decide_escalation(
            _alert(level="L1", axis="behavior", rule="no_event_for>=2h_with_active_session")
        )
        self.assertEqual(level, E3_INFRA_OPS)
        self.assertEqual(owners, ["tristan"])

    def test_whitelist_missing_is_infra_class(self) -> None:
        level, _ = decide_escalation(_alert(level="L2", axis="behavior", rule="whitelist_missing"))
        self.assertEqual(level, E3_INFRA_OPS)

    def test_unacked_escalates_past_infra_branch(self) -> None:
        # 非基建类未闭环: E2 上抬时跳过 E3(Tristan 专管基建维稳), 直接进智囊团
        level, owners = decide_escalation(_alert(level="L2"), unacked_hours=ACK_TIMEOUT_HOURS)
        self.assertEqual(level, E4_THINK_TANK)
        self.assertEqual(owners, ["operating_system"])

    def test_infra_unacked_escalates_to_think_tank(self) -> None:
        level, _ = decide_escalation(
            _alert(level="L2", axis="behavior", rule="whitelist_missing"),
            unacked_hours=ACK_TIMEOUT_HOURS,
        )
        self.assertEqual(level, E4_THINK_TANK)

    def test_l1_unacked_goes_to_unit_lead(self) -> None:
        level, owners = decide_escalation(_alert(level="L1"), unacked_hours=ACK_TIMEOUT_HOURS)
        self.assertEqual(level, E2_UNIT_LEAD)
        self.assertEqual(owners, ["stella"])

    def test_repeat_reaches_think_tank(self) -> None:
        level, owners = decide_escalation(_alert(level="L1"), repeat_count=3)
        self.assertEqual(level, E4_THINK_TANK)
        self.assertEqual(owners, ["operating_system"])

    def test_l3_long_unacked_reaches_founder(self) -> None:
        level, owners = decide_escalation(_alert(level="L3"), unacked_hours=24)
        self.assertEqual(level, E5_FOUNDER)
        self.assertEqual(owners, ["founder"])

    def test_l2_long_unacked_stops_at_think_tank(self) -> None:
        # 未到 L3 不越级到创始人(军团自主治理范围内)
        level, _ = decide_escalation(_alert(level="L2"), unacked_hours=24)
        self.assertEqual(level, E4_THINK_TANK)


class TestS34Reconciliation(unittest.TestCase):
    """S3-4 Nova 接账与 N-01 铁律。"""

    def test_local_estimate_requires_reconciliation(self) -> None:
        self.assertTrue(needs_reconciliation(_alert(cost_basis=COST_BASIS_LOCAL)))

    def test_official_ledger_needs_none(self) -> None:
        self.assertFalse(needs_reconciliation(_alert(cost_basis=COST_BASIS_OFFICIAL)))

    def test_behavior_only_alert_needs_none(self) -> None:
        self.assertFalse(
            needs_reconciliation(_alert(axis="behavior", cost_basis=COST_BASIS_LOCAL))
        )


class _OutboxCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="lao-s3-")
        self.outbox = AlertOutbox(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


class TestS32Outbox(_OutboxCase):
    """S3-2 送达机制: 原子写与消息体。"""

    def test_dispatch_writes_message_file(self) -> None:
        record = self.outbox.dispatch(_alert(), now=NOW)
        path = self.outbox.message_path("a1")
        self.assertTrue(os.path.isfile(path))
        with open(path, "r", encoding="utf-8") as fh:
            on_disk = json.load(fh)
        self.assertEqual(on_disk["alert_id"], "a1")
        self.assertEqual(on_disk["delivered_to"], [RECIPIENT_NOVA])
        self.assertEqual(on_disk["dispatched_at"], "2026-08-30T15:00:00+00:00")
        self.assertEqual(record["escalation_level"], E1_UNIT_AGENTS)

    def test_no_temp_file_left_behind(self) -> None:
        self.outbox.dispatch(_alert(), now=NOW)
        leftovers = [n for n in os.listdir(self.root) if n.startswith(".tmp-")]
        self.assertEqual(leftovers, [])

    def test_message_carries_escalation_fields(self) -> None:
        record = self.outbox.dispatch(_alert(level="L2"), now=NOW)
        self.assertEqual(record["escalation_level"], E2_UNIT_LEAD)
        self.assertEqual(record["escalated_to"], "stella")
        self.assertEqual(record["escalation_path"], [E1_UNIT_AGENTS, E2_UNIT_LEAD])
        self.assertEqual(record["status"], "escalated")

    def test_cost_alert_flags_reconciliation(self) -> None:
        record = self.outbox.dispatch(_alert(cost_basis=COST_BASIS_LOCAL), now=NOW)
        self.assertTrue(record["requires_official_reconciliation"])

    def test_redispatch_is_idempotent_on_path(self) -> None:
        self.outbox.dispatch(_alert(), now=NOW)
        self.outbox.dispatch(_alert(), now=NOW + timedelta(hours=1))
        self.assertEqual(self.outbox.list_alert_ids(), ["a1"])

    def test_outbox_root_path_shape(self) -> None:
        self.assertTrue(
            outbox_root("/home/agentuser/share").endswith(
                os.path.join("share", "inbox", "cost-alert-outbox")
            )
        )


class TestS32AckLoop(_OutboxCase):
    """S3-2 送达确认: 读走 + ACK 闭环。"""

    def test_status_open_before_ack(self) -> None:
        self.outbox.dispatch(_alert(axis="both"), now=NOW)
        status = self.outbox.delivery_status("a1", now=NOW)
        self.assertEqual(status.delivered_to, [RECIPIENT_NOVA, RECIPIENT_ETHAN])
        self.assertEqual(status.acked_by, [])
        self.assertFalse(status.closed)

    def test_partial_ack_is_not_closed(self) -> None:
        self.outbox.dispatch(_alert(axis="both"), now=NOW)
        self.outbox.ack("a1", RECIPIENT_NOVA, now=NOW + timedelta(minutes=5))
        status = self.outbox.delivery_status("a1", now=NOW)
        self.assertEqual(status.acked_by, [RECIPIENT_NOVA])
        self.assertEqual(status.missing(), [RECIPIENT_ETHAN])
        self.assertFalse(status.closed)

    def test_full_ack_closes_loop(self) -> None:
        self.outbox.dispatch(_alert(axis="both"), now=NOW)
        self.outbox.ack("a1", RECIPIENT_NOVA, now=NOW)
        self.outbox.ack("a1", RECIPIENT_ETHAN, now=NOW)
        status = self.outbox.delivery_status("a1", now=NOW)
        self.assertTrue(status.closed)
        self.assertEqual(status.missing(), [])

    def test_ack_file_content(self) -> None:
        self.outbox.dispatch(_alert(), now=NOW)
        self.outbox.ack("a1", RECIPIENT_NOVA, now=NOW)
        with open(self.outbox.ack_path("a1", RECIPIENT_NOVA), "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        self.assertEqual(payload["consumer"], RECIPIENT_NOVA)
        self.assertEqual(payload["acked_at"], "2026-08-30T15:00:00+00:00")
        self.assertEqual(payload["message"], "a1.json")

    def test_ack_files_not_listed_as_alerts(self) -> None:
        self.outbox.dispatch(_alert(), now=NOW)
        self.outbox.ack("a1", RECIPIENT_NOVA, now=NOW)
        self.assertEqual(self.outbox.list_alert_ids(), ["a1"])

    def test_pending_reports_timeout_only(self) -> None:
        self.outbox.dispatch(_alert(), now=NOW)
        fresh = self.outbox.pending(NOW + timedelta(hours=1))
        stale = self.outbox.pending(NOW + timedelta(hours=ACK_TIMEOUT_HOURS))
        self.assertEqual(fresh, [])
        self.assertEqual([s.alert_id for s in stale], ["a1"])

    def test_pending_excludes_closed(self) -> None:
        self.outbox.dispatch(_alert(), now=NOW)
        self.outbox.ack("a1", RECIPIENT_NOVA, now=NOW)
        self.assertEqual(self.outbox.pending(NOW + timedelta(hours=8)), [])

    def test_status_of_unknown_alert_is_none(self) -> None:
        self.assertIsNone(self.outbox.delivery_status("nope", now=NOW))


class TestOutboxRetention(_OutboxCase):
    """S3-2 出箱记录保留 ≥7 天(默认只报不删)。"""

    def test_fresh_files_not_expired(self) -> None:
        self.outbox.dispatch(_alert(), now=NOW)
        self.assertEqual(self.outbox.prune(NOW + timedelta(days=3)), [])

    def test_old_file_reported_but_kept_by_default(self) -> None:
        self.outbox.dispatch(_alert(), now=NOW)
        path = self.outbox.message_path("a1")
        old = (NOW - timedelta(days=30)).timestamp()
        os.utime(path, (old, old))
        expired = self.outbox.prune(NOW)
        self.assertEqual(expired, [path])
        self.assertTrue(os.path.isfile(path), "dry_run 默认不得删除出箱记录")

    def test_retention_window_is_seven_days(self) -> None:
        self.assertEqual(OUTBOX_RETENTION_DAYS, 7)


class TestF1F2F3Dispatch(_OutboxCase):
    """F-1/F-2/F-3 三类出箱实证。"""

    def test_f1_cost_alert_to_nova_only(self) -> None:
        record = self.outbox.dispatch(_alert("f1", level="L3", axis="cost"), now=NOW)
        self.assertEqual(record["recipients"], [RECIPIENT_NOVA])
        self.assertEqual(record["axis"], "cost")

    def test_f2_behavior_alert_to_ethan_only(self) -> None:
        record = self.outbox.dispatch(
            _alert("f2", level="L2", axis="behavior", rule="to_provider_in_denied_providers"),
            now=NOW,
        )
        self.assertEqual(record["recipients"], [RECIPIENT_ETHAN])

    def test_f3_dual_alert_to_both(self) -> None:
        record = self.outbox.dispatch(
            _alert("f3", level="L3", axis="both", rule="to_provider_in_denied_providers"),
            now=NOW,
        )
        self.assertEqual(record["recipients"], [RECIPIENT_NOVA, RECIPIENT_ETHAN])
        self.assertEqual(record["ack_required_from"], [RECIPIENT_NOVA, RECIPIENT_ETHAN])

    def test_batch_dispatch_keeps_three_streams_separate(self) -> None:
        alerts = [
            _alert("b1", axis="cost"),
            _alert("b2", axis="behavior"),
            _alert("b3", axis="both"),
        ]
        records = self.outbox.dispatch_many(alerts, now=NOW)
        routing = {r["alert_id"]: r["recipients"] for r in records}
        self.assertEqual(routing["b1"], [RECIPIENT_NOVA])
        self.assertEqual(routing["b2"], [RECIPIENT_ETHAN])
        self.assertEqual(routing["b3"], [RECIPIENT_NOVA, RECIPIENT_ETHAN])
        self.assertEqual(sorted(self.outbox.list_alert_ids()), ["b1", "b2", "b3"])


class TestBypassDiscipline(unittest.TestCase):
    """铁律: 只写自家出箱目录, 不推外部消息渠道。"""

    def test_dispatch_only_touches_outbox_root(self) -> None:
        parent = tempfile.mkdtemp(prefix="lao-s3-scope-")
        try:
            root = os.path.join(parent, "outbox")
            AlertOutbox(root).dispatch(_alert(), now=NOW)
            self.assertEqual(sorted(os.listdir(parent)), ["outbox"])
            self.assertEqual(os.listdir(root), ["a1.json"])
        finally:
            shutil.rmtree(parent, ignore_errors=True)

    def test_no_network_or_subprocess_imports(self) -> None:
        from .. import router

        with open(router.__file__, "r", encoding="utf-8") as fh:
            source = fh.read()
        for forbidden in ("import requests", "import socket", "import subprocess", "urllib"):
            self.assertNotIn(forbidden, source, f"哨兵不得越权推送: {forbidden}")


if __name__ == "__main__":
    unittest.main()
