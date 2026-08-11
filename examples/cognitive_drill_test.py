#!/usr/bin/env python3
"""
LAO 三层认知系统 — 9 Agent 军演测试(P0-19)
=============================================

6 项测试(真实运行·审计):
  ① L1 冲突修正(403 → 路由避让)
  ② L1 错误复利(2 次 → 自动升级 L2)
  ③ L1 经验复利(成功 → trigger_weight +0.3)
  ④ L2 品味(今天 vs 昨天 vs 本周)
  ⑤ L3 世界观(Tier0 永固不可动)
  ⑥ 完整闭环(L1→L2→L3→综合查询)

输出: 真实运行结果 + 审计结论。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from lao.effect_anchored.cognitive_engine import CognitiveSystem
from lao.effect_anchored.feedback_bus import FeedbackBus, FeedbackEvent


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mk_event(et: str, payload: dict) -> FeedbackEvent:
    return FeedbackEvent(event_type=et, source="drill", payload=payload,
                         severity="info", timestamp=_now())


class DrillReport:
    def __init__(self):
        self.results: list = []

    def add(self, name: str, passed: bool, detail: str):
        self.results.append({
            "test": name, "passed": bool(passed),
            "detail": detail, "at": _now(),
        })
        status = "✅" if passed else "❌"
        print(f"  {status} [{name}] {detail}")

    def audit(self) -> dict:
        total = len(self.results)
        passed = sum(1 for r in self.results if r["passed"])
        return {
            "total": total, "passed": passed,
            "failed": total - passed,
            "verdict": "ALL_PASS" if passed == total else "HAS_FAILURE",
            "conclusion": (
                "三层认知系统编码化+反馈总线融合+军演全部通过"
                if passed == total else "存在未通过项, 需修复"
            ),
        }


def main() -> int:
    report = DrillReport()
    cs = CognitiveSystem()
    fb = FeedbackBus()

    print("=" * 66)
    print("LAO 三层认知系统 · 9 Agent 军演测试")
    print("=" * 66)

    # ── ① L1 冲突修正(403→路由避让) ──────────────────────────────────────
    fp = cs.L1.on_conflict("qwen3.8-max 403", provider="bailian", model="qwen3.8-max")
    is_blocked = fp in cs._conflicts and cs._conflicts[fp]["provider"] == "bailian"
    report.add("①L1冲突修正", is_blocked,
               f"403→避让, 冲突指纹={fp[:18]}…(冲突数={len(cs._conflicts)})")

    # ── ② L1 错误复利(2次→自动升级L2) ────────────────────────────────────
    cs.L1.on_error("timeout-高峰", "第1次")
    second = cs.L1.on_error("timeout-高峰", "第2次(升级)")
    escalated = cs._temporary_constraints["timeout-高峰"]["escalated"]
    report.add("②L1错误复利", escalated,
               f"错误[{second}]2次证据→升级(escalated={escalated})")

    # ── ③ L1 经验复利(成功→+0.3) ─────────────────────────────────────────
    w1 = cs.L1.on_success("refund-rule", delta=0.3)
    w2 = cs.L1.on_success("refund-rule", delta=0.3)
    report.add("③L1经验复利", abs(w2 - w1 - 0.3) < 1e-9,
               f"成功2次 trigger_weight {w1}→{w2}(每次+0.3)")

    # ── ④ L2 品味(今天vs昨天vs本周) ─────────────────────────────────────
    cs.L2.ingest({"task": "退款", "model": "deepseek", "satisfied": True, "recency": "today"})
    cs.L2.ingest({"task": "翻译", "model": "qwen", "satisfied": False, "recency": "week"})
    taste_refund = cs.L2.taste("退款")
    taste_translate = cs.L2.taste("翻译")
    report.add("④L2品味", taste_refund > 0 and taste_translate > 0,
               f"退款相关性={taste_refund}·翻译相关性={taste_translate}(近期经验数={len(cs._recent_experiences)})")

    # ── ⑤ L3 世界观(Tier0不可动) ────────────────────────────────────────
    cs.register_unalterable("客户信任优先")
    tier0_unchanged = cs.L3.is_unalterable({"trust_weight": 0.95, "status": "permanent"})
    tier0_not_weak = not cs.L3.is_unalterable({"trust_weight": 0.4})
    report.add("⑤L3世界观Tier0", tier0_unchanged and tier0_not_weak,
               f"Tier0(0.95/永固)不可动={tier0_unchanged}·普通(0.4)可改={tier0_not_weak}→不可动?")

    # ── ⑥ 完整闭环(L1→L2→L3→查询) ──────────────────────────────────────
    # 通过 FeedbackBus emit 走完整自动分发(同一实例 fb.cognitive)
    fb.emit(_mk_event("conflict", {"provider": "bailian", "model": "qwen3.8-max",
                                   "error_signature": "403-again"}))
    fb.emit(_mk_event("conflict", {"provider": "deepseek", "model": "deepseek-v4-pro",
                                   "error_signature": "403-elsewhere"}))
    fb.emit(_mk_event("error", {"error_signature": "latency-2000"}))
    fb.emit(_mk_event("error", {"error_signature": "latency-2000"}))
    fb.emit(_mk_event("pattern", {"anchor_id": "refund-rule2"}))
    fb.emit(_mk_event("pattern", {"task": "退款"}))
    fb.cognitive.register_unalterable("数据主权")
    r = fb.retrieve("退款")
    ci = len(fb.cognitive._conflicts) >= 2
    ri = r["l2"] > 0
    full = ci and ri and (",".join(str(v) for v in r["weights"].values()) == "0.4,0.35,0.25")
    report.add("⑥完整闭环", full,
               f"L1冲突数={ci}·L2命中={ri}·三层权重=0.4/0.35/0.25")

    # ── 审计结论 ─────────────────────────────────────────────────────────
    audit = report.audit()
    print("\n" + "=" * 66)
    print("审计结论")
    print("=" * 66)
    print(f"  测试项: {audit['total']} | 通过: {audit['passed']} | 失败: {audit['failed']}")
    print(f"  判定: {audit['verdict']}")
    print(f"  结论: {audit['conclusion']}")
    print(f"  三层状态快照: {json.dumps(cs.snapshot(), ensure_ascii=False)}")
    print("=" * 66)

    # 输出完整 report JSON(供报告引用)
    print("\n" + json.dumps({
        "report": report.results, "audit": audit,
    }, ensure_ascii=False, indent=2))

    return 0 if audit["passed"] == audit["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
