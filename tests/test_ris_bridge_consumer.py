# v3.5.2-laorefactor: R4
"""R4.1 ris-bridge 消费者测试（PRD v1.1 第5节任务2·2026-08-19）。

覆盖3用例:
  1. 消费统计: consume_bridge 返回事件数/恢复数统计
  2. 经验灌入: recovery 事件 → ExperienceExtractor 格式入经验库(JSONL)
  3. 黑名单更新: provider_unavailable/isolation → 黑名单生效·provider_ok 解除

全部使用临时目录·不写生产共享态·不 import ris 包(物理隔离铁律)。
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.ris_bridge_consumer import (  # noqa: E402
    consume_bridge,
)


def _bridge(events, isolated=None, summary_extra=None):
    summary = {"isolated_providers": isolated or []}
    summary.update(summary_extra or {})
    return {
        "layer": "ris", "schema_version": "1.0", "generated_at": "2026-08-19T00:00:00+00:00",
        "summary": summary,
        "recent_events": events,
        "active_alerts": [],
    }


class ConsumeBridgeTestBase(unittest.TestCase):
    """公共夹具: 临时桥文件/经验库/黑名单路径。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bridge_file = os.path.join(self.tmp, "ris-bridge.json")
        self.store_path = os.path.join(self.tmp, "lao_experiences.jsonl")
        self.blacklist_path = os.path.join(self.tmp, "provider-blacklist.json")

    def _write_bridge(self, bridge):
        with open(self.bridge_file, "w", encoding="utf-8") as f:
            json.dump(bridge, f, ensure_ascii=False)

    def _consume(self):
        return consume_bridge(bridge_file=self.bridge_file,
                              store_path=self.store_path,
                              blacklist_path=self.blacklist_path)


class TestConsumeStats(ConsumeBridgeTestBase):
    """用例1: 消费统计。"""

    def test_stats_counts(self):
        """consume_bridge → 统计窗口事件数/恢复事件数·桥缺失 fail-open。"""
        events = [
            {"event_type": "gateway_down", "agent_id": "gateway", "ts": "2026-08-19T01:00:00+00:00"},
            {"event_type": "cpu_recovery", "agent_id": "cpu",
             "status": "recovered", "ts": "2026-08-19T02:00:00+00:00"},
            {"event_type": "provider_unavailable", "agent_id": "token-plan",
             "detail": {"reason": "429"}, "ts": "2026-08-19T03:00:00+00:00"},
        ]
        self._write_bridge(_bridge(events))
        stats = self._consume()
        self.assertTrue(stats["ok"])
        self.assertEqual(stats["events_consumed"], 3)
        self.assertEqual(stats["recovery_events"], 1)
        self.assertEqual(stats["experiences_ingested"], 1)
        # 桥文件缺失 → fail-open(ok=False·不抛)
        missing = consume_bridge(bridge_file=os.path.join(self.tmp, "nope.json"),
                                 store_path=self.store_path,
                                 blacklist_path=self.blacklist_path)
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["events_consumed"], 0)


class TestExperienceIngestion(ConsumeBridgeTestBase):
    """用例2: 恢复经验灌入经验库(ExperienceExtractor 格式)。"""

    def test_recovery_experiences_written(self):
        """recovery 事件 → 经验库 JSONL 新增·ExperienceExtractor 字段齐全。"""
        events = [
            {"event_type": "cpu_recovery", "agent_id": "cpu",
             "status": "recovered", "severity": "warning",
             "detail": {"cpu_pct": 91.2, "action": "restart_worker", "attempts": 1},
             "ts": "2026-08-19T02:00:00+00:00"},
            {"event_type": "mcp_leak", "agent_id": "mcp",
             "status": "recovered",
             "detail": {"leaked": 3, "action": "kill_sessions"},
             "ts": "2026-08-19T02:30:00+00:00"},
        ]
        self._write_bridge(_bridge(events))
        stats = self._consume()
        self.assertEqual(stats["experiences_ingested"], 2)
        # 经验库落盘: ExperienceExtractor JSONL 格式
        self.assertTrue(os.path.exists(self.store_path))
        with open(self.store_path, encoding="utf-8") as f:
            records = [json.loads(ln) for ln in f if ln.strip()]
        self.assertEqual(len(records), 2)
        rec = records[0]
        for field in ("experience_id", "experience_content", "task_type",
                      "agent_id", "isolation_key", "quality_grade"):
            self.assertIn(field, rec)
        self.assertEqual(rec["agent_id"], "cpu")
        self.assertEqual(rec["isolation_key"], "cpu")
        self.assertIn("RIS恢复经验", rec["experience_content"])
        # 幂等: 同桥二次消费 → 去重(同 fingerprint+agent_id 覆盖·不重复增长)
        self._consume()
        with open(self.store_path, encoding="utf-8") as f:
            again = [json.loads(ln) for ln in f if ln.strip()]
        self.assertEqual(len(again), 2)


class TestBlacklistUpdate(ConsumeBridgeTestBase):
    """用例3: provider 黑名单更新(隔离键)。"""

    def test_blacklist_isolation_and_release(self):
        """provider_unavailable/isolation → 黑名单生效·provider_ok 解除。"""
        events = [
            {"event_type": "provider_unavailable", "agent_id": "token-plan",
             "detail": {"reason": "429"}, "ts": "2026-08-19T01:00:00+00:00"},
            {"event_type": "provider_isolation", "agent_id": "novarouteai",
             "detail": {"circuit_open": True}, "ts": "2026-08-19T01:30:00+00:00"},
        ]
        self._write_bridge(_bridge(events))
        stats = self._consume()
        self.assertEqual(stats["blacklist_active"], ["novarouteai", "token-plan"])
        # 黑名单持久化(隔离键)
        with open(self.blacklist_path, encoding="utf-8") as f:
            bl = json.load(f)
        self.assertTrue(bl["token-plan"]["isolated"])
        self.assertTrue(bl["novarouteai"]["isolated"])
        self.assertEqual(bl["token-plan"]["events"], 1)

        # provider_ok 事件 → 解除隔离(isolated=false·保留轨迹)
        events.append({"event_type": "provider_ok", "agent_id": "token-plan",
                       "detail": {"code": 200}, "ts": "2026-08-19T04:00:00+00:00"})
        self._write_bridge(_bridge(events))
        stats = self._consume()
        self.assertEqual(stats["blacklist_active"], ["novarouteai"])
        self.assertEqual(stats["providers_released"], ["token-plan"])
        with open(self.blacklist_path, encoding="utf-8") as f:
            bl = json.load(f)
        self.assertFalse(bl["token-plan"]["isolated"])
        self.assertTrue(bl["novarouteai"]["isolated"])

        # summary.isolated_providers(RIS B5 隔离指令)→ 同样进黑名单
        self._write_bridge(_bridge([], isolated=["deepseek"]))
        stats = self._consume()
        self.assertIn("deepseek", stats["blacklist_active"])
        with open(self.blacklist_path, encoding="utf-8") as f:
            bl = json.load(f)
        self.assertTrue(bl["deepseek"]["isolated"])


if __name__ == "__main__":
    unittest.main()
