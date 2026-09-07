# -*- coding: utf-8 -*-
"""223号施工令 #34 单元测试 · LAO L3 授权确权交易链（用户/协同经验半）

覆盖：P1 human 类打标入库与 raw_trace 并写、P2 10 条阈值、P3 未授权阻断与
Ethan 确权打标、P4 默认关闭与上架件哈希化边界、口径统一（collaborative 别名）。
"""
import json
import os
import shutil
import tempfile
import unittest

from lao.effect_anchored import l3_user_chain as chain


class L3UserChainTest(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="l3home_")
        self.vault = tempfile.mkdtemp(prefix="l3vault_")
        self.zeus = tempfile.mkdtemp(prefix="l3zeus_")
        self.ethan = tempfile.mkdtemp(prefix="l3ethan_")   # 隔离：不污染生产收件箱
        self.owner = "founder"
        self.domain = "collab/decision"

    def tearDown(self):
        for d in (self.home, self.vault, self.zeus, self.ethan):
            shutil.rmtree(d, ignore_errors=True)
        os.environ.pop(chain.TRADE_FLAG, None)

    def _confirm(self, **kw):
        return chain.confirm_rights(self.owner, self.domain, home=self.home,
                                    ethan_dir=self.ethan, **kw)

    def _record(self, n, outcome="success", domain=None):
        out = []
        for i in range(n):
            out.append(chain.record_user_experience(
                self.owner, domain or self.domain,
                "统筹席裁定第%d条：口径以签章件为准" % i,
                outcome=outcome, home=self.home, vault_root=self.vault,
                raw_trace=False))
        return out

    # ── P1 ──────────────────────────────────────────────────────────────
    def test_p1_human_type_into_anchors(self):
        rec = self._record(1)[0]
        self.assertEqual(rec["experience_type"], chain.TYPE_COLLABORATIVE)
        with open(os.path.join(self.home, "anchors.json"), encoding="utf-8") as f:
            data = json.load(f)
        cur = data[rec["anchor_id"]]["current"]
        self.assertEqual(cur["experience_type"], "collaborative")
        self.assertEqual(cur["anchor_type"], "fact")
        self.assertIn("l3-user-chain", cur["tags"])

    def test_p1_rejects_bad_type(self):
        with self.assertRaises(ValueError):
            chain.record_user_experience(self.owner, self.domain, "x",
                                        experience_type="agent_runtime",
                                        home=self.home, raw_trace=False)

    def test_p1_raw_trace_default_closed_without_consent(self):
        rec = chain.record_user_experience(
            self.owner, self.domain, "无授权时原卷账零写入",
            home=self.home, vault_root=self.vault, raw_trace=True)
        self.assertFalse(rec["raw_trace"]["logged"])
        self.assertEqual(rec["raw_trace"]["reason"], "no_consent")
        self.assertFalse(os.path.exists(os.path.join(self.vault, "ledger.jsonl")))

    def test_p1_raw_trace_dual_write_with_consent(self):
        from lao.effect_anchored.raw_trace.consent import RawTraceConsent
        RawTraceConsent(self.vault).grant(
            authorizer=self.owner, scope="founder_collaboration_trace",
            retention_days=365)
        rec = chain.record_user_experience(
            self.owner, self.domain, "授权有效时原卷账+引用登记双写",
            home=self.home, vault_root=self.vault, raw_trace=True)
        self.assertTrue(rec["raw_trace"]["logged"], rec["raw_trace"])
        self.assertTrue(rec["raw_trace"]["registered"], rec["raw_trace"])
        man = os.path.join(self.vault, "references", "manifest.json")
        self.assertTrue(os.path.exists(man))
        with open(man, encoding="utf-8") as f:
            m = json.load(f)
        self.assertEqual(m["schema"], "ral-raw-trace-manifest/1.0")
        # artifact_vid 取 "<card_id>@<version>"；不依赖 manifest 字段层级路径
        refs = open(os.path.join(self.vault, "references", "references.jsonl"),
                    encoding="utf-8").read()
        self.assertIn("%s@1" % rec["anchor_id"], refs)

    # ── P2 ──────────────────────────────────────────────────────────────
    def test_p2_threshold_10_not_triggered_at_9(self):
        self._record(9)
        st = chain.threshold_status(self.owner, home=self.home)
        self.assertEqual(st["domains"][self.domain]["count"], 9)
        self.assertFalse(st["domains"][self.domain]["reached"])
        r = chain.request_authorization(self.owner, self.domain, home=self.home)
        self.assertFalse(r["requested"])
        self.assertEqual(r["reason"], "below_threshold")

    def test_p2_threshold_10_triggered_error_and_success_both_count(self):
        self._record(6, outcome="success")
        self._record(4, outcome="error")
        st = chain.threshold_status(self.owner, home=self.home)
        slot = st["domains"][self.domain]
        self.assertEqual((slot["count"], slot["success"], slot["error"]),
                         (10, 6, 4))
        self.assertTrue(slot["reached"])
        r = chain.request_authorization(self.owner, self.domain, home=self.home)
        self.assertTrue(r["requested"])
        # 幂等：同批不重复请求
        r2 = chain.request_authorization(self.owner, self.domain, home=self.home)
        self.assertFalse(r2["requested"])
        self.assertTrue(r2.get("dedup"))

    def test_p2_threshold_is_per_owner_and_domain(self):
        self._record(10, domain="collab/decision")
        self._record(3, domain="collab/other")
        st = chain.threshold_status(self.owner, home=self.home)
        self.assertEqual(st["reached_domains"], ["collab/decision"])

    # ── P3 ──────────────────────────────────────────────────────────────
    def test_p3_confirm_blocked_without_authorization(self):
        self._record(10)
        res = self._confirm()
        self.assertFalse(res["ok"])
        self.assertTrue(res["blocked"])
        self.assertEqual(res["confirmed"], [])
        self.assertFalse(os.path.exists(os.path.join(
            self.home, "data", "ethan_notarizations.jsonl")))
        self.assertFalse(os.listdir(self.ethan))   # Ethan 收件箱零写入

    def test_p3_confirm_after_authorization_marks_rights(self):
        recs = self._record(10)
        chain.grant_authorization(self.owner, self.domain, home=self.home)
        res = self._confirm()
        self.assertTrue(res["ok"])
        self.assertEqual(res["count"], 10)
        confirmed = chain.human_anchors(owner=self.owner, home=self.home,
                                        confirmed=True)
        self.assertEqual(len(confirmed), 10)
        one = next(a for a in confirmed
                   if a["anchor_id"] == recs[0]["anchor_id"])
        self.assertIn("rights:confirmed", one["tags"])
        self.assertTrue(one["value"]["rights"]["attestation"].startswith("sha256:"))
        self.assertEqual(one["value"]["rights"]["trade_venue"], "zeus")
        # 确权后不再计入待授权阈值
        st = chain.threshold_status(self.owner, home=self.home)
        self.assertNotIn(self.domain, st["reached_domains"])

    # ── P4 ──────────────────────────────────────────────────────────────
    def test_p4_publish_default_closed(self):
        self._record(10)
        chain.grant_authorization(self.owner, self.domain, home=self.home)
        self._confirm()
        res = chain.publish_to_zeus(self.owner, self.domain, home=self.home,
                                    inputs_dir=self.zeus)
        self.assertFalse(res["published"])
        self.assertEqual(res["reason"], "feature_flag_off")
        self.assertFalse(os.listdir(self.zeus))

    def test_p4_publish_blocked_when_trade_not_granted(self):
        self._record(10)
        chain.grant_authorization(self.owner, self.domain, home=self.home,
                                  stages=("upload",))
        self._confirm()
        res = chain.publish_to_zeus(self.owner, self.domain, home=self.home,
                                    enabled=True, inputs_dir=self.zeus)
        self.assertFalse(res["published"])
        self.assertTrue(res["blocked"])
        self.assertFalse(os.listdir(self.zeus))

    def test_p4_publish_only_hashed_metadata(self):
        self._record(2)
        chain.grant_authorization(self.owner, self.domain, home=self.home)
        self._confirm()
        res = chain.publish_to_zeus(self.owner, self.domain, home=self.home,
                                    enabled=True, inputs_dir=self.zeus)
        self.assertTrue(res["published"])
        self.assertEqual(res["count"], 2)
        raw = open(os.path.join(self.zeus, "l3_trade_listings.jsonl"),
                   encoding="utf-8").read()
        self.assertNotIn("统筹席裁定", raw)          # 经验原文不出本机
        self.assertNotIn("trigger_condition", raw)
        line = json.loads(raw.splitlines()[0])
        self.assertEqual(line["schema"], "l3-trade-listing/1.0")
        self.assertEqual(line["trade_venue"], "zeus")
        self.assertEqual(line["revenue_formula"], "user_50_platform_50")
        self.assertEqual(len(line["content_sha256"]), 64)
        self.assertIn("@", line["artifact_vid"])

    # ── 口径统一 ─────────────────────────────────────────────────────────
    def test_classifier_accepts_collaborative_alias(self):
        from lao.effect_anchored.evolution.experience_classifier import (
            CHANNEL_ETHAN_RIGHTS, ExperienceCategory, ExperienceClassifier,
        )
        c = ExperienceClassifier()
        self.assertIs(c.classify({"experience_type": "collaborative"}),
                      ExperienceCategory.HUMAN_AGENT_COLLAB)
        self.assertIs(c.classify({"experience_type": "user_personal"}),
                      ExperienceCategory.USER_PERSONAL)
        r = c.classify_and_route({"experience_type": "collaborative"})
        self.assertEqual(r.channel, CHANNEL_ETHAN_RIGHTS)
        self.assertTrue(r.authorization_required)

    def test_chain_status_reports_flag_and_venue(self):
        self._record(1)
        st = chain.chain_status(self.owner, home=self.home)
        self.assertEqual(st["threshold"], 10)
        self.assertEqual(st["trade_venue"], "zeus")
        self.assertFalse(st["trade_flag"]["on"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
