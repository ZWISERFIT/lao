# -*- coding: utf-8 -*-
"""后训练第一批（A1＋A2＋D1）七门硬测试

门1 默认关闭零写入      门2 授权门禁（缺记录拒写/撤销后拒写）
门3 脱敏钩子（含哈希不误伤）   门4 保留期到期只标失效不物理删
门5 零遥测（静态扫描无出站依赖）  门6 A2对齐件②登记口径
门7 D1撤回贯通＋审计回放

依据：一页施工方案第二节七条验收口径（统筹席核批·开工令②：全部硬门）。
"""
import json
import os
import re
import tempfile
import unittest

from lao.effect_anchored.raw_trace.consent import ConsentDenied, RawTraceConsent
from lao.effect_anchored.raw_trace.redaction import redact_payload
from lao.effect_anchored.raw_trace.vault import RawTraceVault
from lao.effect_anchored.raw_trace.registry import (
    ReferenceRegistry, ReferenceLedgerError)
from lao.effect_anchored.raw_trace.withdraw import WithdrawalGateway
from lao.effect_anchored.raw_trace import hook


HERE = os.path.dirname(os.path.abspath(__file__))
MODULE_DIR = os.path.join(os.path.dirname(HERE), "lao", "effect_anchored",
                          "raw_trace")


class Gate1DefaultOffTest(unittest.TestCase):
    """门1：默认关闭——无授权记录时全流程零写入。"""

    def test_append_denied_and_zero_write(self):
        root = tempfile.mkdtemp(prefix="rt-g1-")
        vault = RawTraceVault(root)
        with self.assertRaises(ConsentDenied):
            vault.append("probe", {"x": 1}, source="test")
        # 零写入硬证：账本/索引/审计文件均不得出现
        for name in ("ledger.jsonl", "index.json", "audit.jsonl"):
            self.assertFalse(os.path.exists(os.path.join(root, name)),
                             "默认关闭时出现写盘：%s" % name)
        self.assertFalse(os.path.exists(os.path.join(root, "consent.jsonl")))

    def test_registry_and_samples_denied(self):
        root = tempfile.mkdtemp(prefix="rt-g1b-")
        obj = os.path.join(root, "card.json")
        with open(obj, "w") as f:
            f.write("{}")
        reg = ReferenceRegistry(root)
        with self.assertRaises(ConsentDenied):
            reg.register("CWF-T01", 1, "did:zwf:t", "collaborative",
                         "t", "internal", obj, "user_50_platform_50")
        gw = WithdrawalGateway(root)
        with self.assertRaises(ConsentDenied):
            gw.record_sample("CWF-T01@1", "smp-1", "sft")


class Gate2ConsentTest(unittest.TestCase):
    """门2：授权门禁——开启必须授权记录；撤销后拒写。"""

    def test_grant_then_append_ok(self):
        root = tempfile.mkdtemp(prefix="rt-g2-")
        vault = RawTraceVault(root)
        rec = vault.consent.grant("founder", "raw_trace_archive", 30)
        self.assertTrue(rec["record_id"])
        e = vault.append("probe", {"k": "v"}, source="test")
        self.assertEqual(e["seq"], 1)
        self.assertEqual(e["consent_record_id"], rec["record_id"])

    def test_incomplete_grant_refused(self):
        root = tempfile.mkdtemp(prefix="rt-g2b-")
        c = RawTraceConsent(root)
        with self.assertRaises(ConsentDenied):
            c.grant("", "scope", 30)          # 缺授权人
        with self.assertRaises(ConsentDenied):
            c.grant("who", "", 30)            # 缺范围
        with self.assertRaises(ConsentDenied):
            c.grant("who", "scope", 0)        # 缺有效保留期

    def test_revoke_blocks_append(self):
        root = tempfile.mkdtemp(prefix="rt-g2c-")
        vault = RawTraceVault(root)
        rec = vault.consent.grant("founder", "raw_trace_archive", 30)
        vault.append("probe", {"k": 1}, source="test")
        vault.consent.revoke(rec["record_id"], actor="founder")
        with self.assertRaises(ConsentDenied):
            vault.append("probe2", {"k": 2}, source="test")


class Gate3RedactionTest(unittest.TestCase):
    """门3：脱敏钩子在写入路径上；纯哈希不误伤。"""

    def test_redact_on_write(self):
        root = tempfile.mkdtemp(prefix="rt-g3-")
        vault = RawTraceVault(root)
        vault.consent.grant("founder", "raw_trace_archive", 30)
        e = vault.append("probe", {
            "msg": "联系我 a@b.com 电话13812345678 服务器10.1.2.3",
        }, source="test")
        p = e["payload"]["msg"]
        self.assertIn("[REDACTED_EMAIL]", p)
        self.assertIn("[REDACTED_PHONE]", p)
        self.assertIn("[REDACTED_IP]", p)
        self.assertNotIn("a@b.com", p)

    def test_hash_preserved(self):
        sha = "5d5adf6541580f72ca3e1aaa916799153cdf3afd07c0bd6ffb302420561529c8"
        self.assertEqual(len(sha), 64)  # 自验：必须是合法64位hex
        out = redact_payload({"sha256": sha})
        self.assertEqual(out["sha256"], sha, "纯哈希被误脱敏")


class Gate4RetentionTest(unittest.TestCase):
    """门4：保留期到期只标失效，不物理删除。"""

    def test_expire_marks_not_deletes(self):
        root = tempfile.mkdtemp(prefix="rt-g4-")
        vault = RawTraceVault(root)
        vault.consent.grant("founder", "raw_trace_archive", 30)
        vault.append("probe", {"k": 1}, source="test")
        # 将索引中到期日改为过去，模拟保留期到期
        idx = json.load(open(vault.index_path))
        idx["entries"][0]["expires_at"] = "2000-01-01T00:00:00+00:00"
        json.dump(idx, open(vault.index_path, "w"), ensure_ascii=False)
        expired = vault.expire_due()
        self.assertEqual(expired, [1])
        # 账本原行仍在（不物理删）
        lines = open(vault.ledger_path).read().strip().splitlines()
        self.assertEqual(len(lines), 1)
        # 默认读不出失效条目；带 include_expired 可见
        self.assertEqual(len(vault.read_entries()), 0)
        self.assertEqual(len(vault.read_entries(include_expired=True)), 1)
        st = vault.stats()
        self.assertEqual((st["active"], st["expired"]), (0, 1))


class Gate5NoTelemetryTest(unittest.TestCase):
    """门5：零遥测——静态扫描本模块无任何出站网络依赖。"""

    FORBIDDEN = re.compile(
        r"^\s*(?:import|from)\s+"
        r"(?:socket|urllib|requests|http\.client|httpx|aiohttp|telnetlib|"
        r"ftplib|smtplib|xmlrpc)|"
        r"(?:requests\.|urllib\.request|socket\.connect|http\.client\.|urlopen)",
        re.MULTILINE)

    def test_no_outbound_symbols(self):
        hits = []
        for name in sorted(os.listdir(MODULE_DIR)):
            if not name.endswith(".py"):
                continue
            src = open(os.path.join(MODULE_DIR, name), encoding="utf-8").read()
            m = self.FORBIDDEN.search(src)
            if m:
                hits.append((name, m.group(0)))
        self.assertEqual(hits, [], "发现出站网络依赖：%s" % hits)


class Gate6A2AlignmentTest(unittest.TestCase):
    """门6：A2双写对齐件②登记口径。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="rt-g6-")
        RawTraceConsent(self.root).grant("founder", "raw_trace_archive", 30)
        self.obj = os.path.join(self.root, "CWF-T09.json")
        with open(self.obj, "w") as f:
            json.dump({"card_id": "CWF-T09", "schema":
                       "ral-cognitive-workflow-card/1.0"}, f)
        self.reg = ReferenceRegistry(self.root)

    def test_register_aligns_item2_schema(self):
        e = self.reg.register("CWF-T09", 1, "did:zwf:founder", "collaborative",
                              "founder", "internal", self.obj,
                              "user_50_platform_50", actor="founder")
        # 件② ral_memory_card 关键列逐一对齐
        for col in ("card_id", "version", "subject_id", "library", "ownership",
                    "visibility", "state", "origin_path", "origin_sha256",
                    "artifact_vid", "revenue_formula", "ingested_at",
                    "created_at"):
            self.assertIn(col, e, "缺件②对齐列：%s" % col)
        self.assertEqual(len(e["origin_sha256"]), 64)
        self.assertEqual(e["artifact_vid"], "CWF-T09@1")  # 件②口径格式
        self.assertEqual(e["state"], "candidate")
        man = self.reg.manifest()
        self.assertEqual(man["schema"], "ral-raw-trace-manifest/1.0")
        self.assertEqual(man["entries"][0]["origin_sha256"], e["origin_sha256"])

    def test_state_flow_via_log_only(self):
        self.reg.register("CWF-T09", 1, "did:zwf:founder", "collaborative",
                          "founder", "internal", self.obj,
                          "user_50_platform_50", actor="founder")
        self.reg.ratify("CWF-T09", 1, "founder", reason="确权")
        self.assertEqual(self.reg.state_of("CWF-T09", 1), "ratified")
        self.reg.withdraw("CWF-T09", 1, "founder", reason="撤回")
        self.assertEqual(self.reg.state_of("CWF-T09", 1), "withdrawn")
        logs = self.reg.state_log("CWF-T09")
        self.assertEqual([r["to_state"] for r in logs],
                         ["candidate", "ratified", "withdrawn"])
        # 撤回后不可复活（件②口径：撤回记录只能由真实流转产生）
        with self.assertRaises(ReferenceLedgerError):
            self.reg.ratify("CWF-T09", 1, "founder")

    def test_source_ref_no_body_smuggling(self):
        self.reg.register("CWF-T09", 1, "did:zwf:founder", "collaborative",
                          "founder", "internal", self.obj,
                          "user_50_platform_50", actor="founder")
        self.reg.add_source_reference("CWF-T09", 1, "ris_event", "ris-exp-1",
                                      ref_sha256=None)
        with self.assertRaises(ReferenceLedgerError):
            self.reg.add_source_reference("CWF-T09", 1, "ris_event",
                                          "x" * 200)  # 超长=夹带正文


class Gate7D1WithdrawTest(unittest.TestCase):
    """门7：D1撤回贯通——撤卡→卷引用与样本联动失效，审计可回放。"""

    def test_full_chain(self):
        root = tempfile.mkdtemp(prefix="rt-g7-")
        RawTraceConsent(root).grant("founder", "raw_trace_archive", 30)
        obj = os.path.join(root, "CWF-T10.json")
        with open(obj, "w") as f:
            f.write("{}")
        reg = ReferenceRegistry(root)
        reg.register("CWF-T10", 1, "did:zwf:founder", "collaborative",
                     "founder", "internal", obj,
                     "user_50_platform_50", actor="founder")
        gw = WithdrawalGateway(root)
        gw.record_sample("CWF-T10@1", "smp-1", "sft")
        gw.record_sample("CWF-T10@1", "smp-2", "preference")
        self.assertEqual(len(gw.effective_samples()), 2)
        audit = gw.withdraw_card("CWF-T10", 1, "founder", reason="创始人撤回")
        self.assertEqual(reg.state_of("CWF-T10", 1), "withdrawn")
        self.assertEqual(audit["samples_invalidated"], 2)
        self.assertEqual(len(gw.effective_samples()), 0)
        # 样本行未物理删除（追加式账）
        self.assertGreaterEqual(len(gw.samples()), 4)
        replay = gw.replay()
        self.assertEqual(len(replay), 1)
        self.assertEqual(replay[0]["card_id"], "CWF-T10")
        self.assertEqual(replay[0]["samples_invalidated"], 2)


class HookGuardTest(unittest.TestCase):
    """挂点守护：默认关闭零影响；开启后双写；异常不上抛。"""

    def test_hook_off_zero_write(self):
        root = tempfile.mkdtemp(prefix="rt-hk-")
        r = hook.register_confirmed_anchor("CWF-T11", "founder", {"a": 1},
                                           vault_root=root)
        self.assertEqual(r, {"logged": False, "registered": False})
        self.assertFalse(os.path.exists(os.path.join(root, "ledger.jsonl")))

    def test_hook_on_dual_write(self):
        root = tempfile.mkdtemp(prefix="rt-hk2-")
        RawTraceConsent(root).grant("founder", "raw_trace_archive", 30)
        obj = os.path.join(root, "CWF-T12.json")
        with open(obj, "w") as f:
            f.write("{}")
        r = hook.register_confirmed_anchor("CWF-T12", "founder", {"a": 1},
                                           card_file=obj, vault_root=root)
        self.assertEqual(r, {"logged": True, "registered": True})
        reg = ReferenceRegistry(root)
        self.assertEqual(reg.state_of("CWF-T12", 1), "candidate")

    def test_hook_never_raises(self):
        # vault_root 指向不可写路径亦不得抛
        r = hook.register_confirmed_anchor("CWF-T13", "founder", {"a": 1},
                                           vault_root="/proc/definitely-no")
        self.assertEqual(r, {"logged": False, "registered": False})


if __name__ == "__main__":
    unittest.main(verbosity=2)
