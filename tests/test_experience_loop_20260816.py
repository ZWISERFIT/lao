"""三层Loop闭环验证 (LAO v3.4 · 2026-08-16 创始人令)

验证 L1(命中率)↔L2(经验工厂)↔L3(确权交易)+RIS(免疫) 全链闭环:
  ① L1: 剪枝保末条消息 / 缓存感知真实激活 / 经验约束反哺避让 / 会话粘性
  ② L2: 锚点容量收敛(Tier0保护) / MemoryAnchor版本历史 / 复利权重可读
        / 幻觉门真实schema校验+违规日志可读
  ③ L3: ErgeWriter版本递增 / 资产持久化 / readiness语义(量先行)
  ④ Loop: 错误→锚点→确权→ERGE/资产→反哺L1; RIS恢复经验→免疫
"""
import json
import os
import sqlite3
import tempfile
import time
from datetime import datetime, timezone, timedelta

import pytest

from lao.effect_anchored.cognitive_anchor import (
    CognitiveAnchorStore, make_decision_anchor, make_fact_anchor,
)
from lao.effect_anchored.experience_loop import ExperienceLoop
from lao.effect_anchored.experience_readiness import (
    ExperienceReadinessTracker, ReadinessConfig,
)
from lao.effect_anchored.feedback_bus import FeedbackBus
from lao.effect_anchored.hallucination_gate import HallucinationGate
from lao.effect_anchored.memory_anchor import MemoryAnchor
from lao.effect_anchored.routing.model_router import ModelRouter


@pytest.fixture()
def loop_home(tmp_path):
    return str(tmp_path / "loop-home")


@pytest.fixture()
def loop(loop_home):
    cfg = ReadinessConfig(min_trigger=2, min_cross_domain=1,
                          min_age_days=0, min_confidence=0.5)
    return ExperienceLoop(home=loop_home, readiness_config=cfg)


# ── ① L1 ──────────────────────────────────────────────────────────────

def test_l1_stabilize_keeps_last_message():
    """剪枝不再丢当前请求消息(旧bug: out[:30] 丢末条)。"""
    from lao.effect_anchored.routing import lao_router_server as m
    msgs = ([{"role": "system", "content": "sys"}]
            + [{"role": "user", "content": f"q{i}"} for i in range(40)])
    out = m._stabilize_messages(msgs, max_history=30)
    assert len(out) == 30
    assert out[-1]["content"] == "q39"          # 当前请求保留
    assert out[0]["role"] == "system"           # 稳定前缀保留


def test_l1_cache_awareness_activates_with_real_text():
    """tier 名直通场景下, 真实任务文本/大上下文激活缓存感知(原死代码)。"""
    r = ModelRouter()
    assert r._cache_awareness("light")["active"] is False              # 旧行为
    assert r._cache_awareness("light", task_text="请分析这段数据")["mode"] == "miss"
    assert r._cache_awareness("light", task_text="模板回复")["mode"] == "hit"
    assert r._cache_awareness("light", context_tokens=9000)["active"] is True


def test_l1_router_applies_feedback_constraints(loop):
    """L2/L3 约束反哺: 错误×2 → provider 避让进 route() 选品。"""
    loop.record_route_result("novarouteai", "deepseek-v4-flash",
                             False, "HTTP 403 forbidden")
    loop.record_route_result("novarouteai", "deepseek-v4-flash",
                             False, "HTTP 403 forbidden")
    router = ModelRouter()
    loop.attach_router(router)
    sel = router.route("light", task_text="模板回复")
    assert sel.provider != "novarouteai"


def test_l1_session_sticky_roundtrip(loop_home):
    from lao.effect_anchored.routing import lao_router_server as m
    msgs = [{"role": "system", "content": "s"},
            {"role": "user", "content": "hello"}]
    fp = m._session_fingerprint(msgs)
    assert fp == m._session_fingerprint(msgs)          # 跨轮稳定
    m._sticky_put(fp, "token-plan", "qwen3.7-plus", "momo")
    entry = m._sticky_get(fp)
    assert entry["provider"] == "token-plan"
    entry["ts"] = time.time() - m.STICKY_TTL_SEC - 1
    with m._sticky_lock:
        m._sticky_load()[fp] = entry
    assert m._sticky_get(fp) is None                   # TTL 过期失效


# ── ② L2 ──────────────────────────────────────────────────────────────

def test_l2_anchor_store_capacity_tier0_protected(tmp_path):
    """容量收敛: 超限淘汰低信任·Tier0(trust>=0.8)永固·历史封顶。"""
    store = CognitiveAnchorStore(str(tmp_path / "a.json"),
                                 max_anchors=5, max_history=2)
    store.put(make_fact_anchor("t0", "永固", trust_weight=0.9))
    for i in range(10):
        store.put(make_fact_anchor(f"weak-{i}", f"v{i}", trust_weight=0.3))
    ids = {a["anchor_id"] for a in store.lookup()}
    assert len(ids) == 5
    assert "t0" in ids                                # Tier0 不淘汰
    a = make_decision_anchor("upd", "p", "t", "a")
    store.put(a); store.put(a); store.put(a)
    assert len(store.get("upd")["version"] and
               store._anchors["upd"]["history"]) <= 2  # 历史封顶


def test_l2_memory_anchor_versions_not_lost(tmp_path):
    """MemoryAnchor 更新不再原地覆盖丢历史(文档承诺 versioned)。"""
    m = MemoryAnchor(str(tmp_path / "m.json"))
    m.put("k", "v1"); m.put("k", "v2")
    assert m.lookup("k").value == "v2"
    hist = m.history("k")
    assert hist and hist[-1]["value"] == "v1"


def test_l2_compound_weights_feed_retrieve():
    """经验复利权重 on_success 写入后可被 retrieve 读到(原写入黑洞)。"""
    from lao.effect_anchored.cognitive_engine import CognitiveSystem
    cs = CognitiveSystem()
    cs.L1.on_success("refund-rule")
    cs.L1.on_success("refund-rule")
    r = cs.retrieve("refund-rule")
    assert r["l1"] > 0


def test_l2_hallucination_gate_real_schema_and_violations(tmp_path):
    """schema 层真实校验(原桩: 任何 dict 直接 PASS) + 违规日志可读。"""
    gate = HallucinationGate(
        violation_log_path=str(tmp_path / "v.jsonl"))
    bad = {"anchor_id": "x", "value": {}}            # 缺 anchor_type
    res = gate.check(bad, expected_schema={
        "type": "object",
        "required": ["anchor_id", "anchor_type", "value"]})
    assert not res.passed
    good = {"anchor_id": "x", "anchor_type": "fact", "value": 1}
    assert gate.check(good, expected_schema={
        "type": "object",
        "required": ["anchor_id", "anchor_type", "value"]}).passed
    assert len(gate.violations()) == 1
    assert gate.violations()[0]["timestamp"] is not None
    assert os.path.exists(tmp_path / "v.jsonl")


# ── ③ L3 ──────────────────────────────────────────────────────────────

def test_l3_erge_writer_versions_increment(tmp_path):
    from lao.effect_anchored.erge_writer import ErgeWriter
    loop = ExperienceLoop(home=str(tmp_path / "h"))   # 自建五表schema
    w = ErgeWriter(db_path=loop.erge_db)
    a = make_fact_anchor("e1", "v1").to_dict()
    w.write_anchor(a)
    w.write_anchor(make_fact_anchor("e1", "v2").to_dict())
    conn = sqlite3.connect(loop.erge_db)
    try:
        versions = [r[0] for r in conn.execute(
            "SELECT version FROM versions WHERE anchor_id='e1' ORDER BY version")]
    finally:
        conn.close()
    assert versions == [1, 2]                        # 原实现恒为 1


def test_l3_asset_registry_persists(tmp_path):
    from lao.effect_anchored.experience_asset import ExperienceAssetRegistry
    p = str(tmp_path / "assets.json")
    reg = ExperienceAssetRegistry(store_path=p)
    a = reg.create("did:zwf:t", "p", "s", verification_pct=90)
    reg2 = ExperienceAssetRegistry(store_path=p)
    assert reg2.get(a.asset_id) is not None
    assert reg2.verify(a.asset_id)


def test_l3_readiness_quantity_first():
    """ready_batch 语义修复: 量判定本地先行·未授权不再阻止计算。"""
    t = ExperienceReadinessTracker(ReadinessConfig(
        min_trigger=1, min_cross_domain=0, min_age_days=0, min_confidence=0.1))
    exps = [{"id": "e1", "trigger_count": 5, "cross_domain": 1,
             "created_at": "2026-08-01T00:00:00+00:00", "confidence": 0.9}]
    ready = t.ready_batch(exps, enforce_consent=False)
    assert len(ready) == 1                            # 未授权也能算量
    with pytest.raises(PermissionError):              # 推送③前才查授权
        t.ready_batch(exps, enforce_consent=True)


# ── ④ 三层Loop闭环 ────────────────────────────────────────────────────

def _ris_store(path, n_success=3, n_fail=2):
    now = "2026-08-16T00:00:00+00:00"
    rows = []
    for i in range(n_success):
        rows.append({"experience_id": f"s{i}", "event_type": "gateway_recovery",
                     "agent_id": "ris", "classified": "gateway_down",
                     "recovered": True, "verified": True, "attempts": 1,
                     "recovery_method": "L1_restart", "category": "infrastructure",
                     "harm": "runtime", "detail": {}, "extracted_at": now})
    for i in range(n_fail):
        rows.append({"experience_id": f"f{i}", "event_type": "cpu_anomaly",
                     "agent_id": "ris", "classified": "cpu_hot",
                     "recovered": False, "verified": False, "attempts": 3,
                     "recovery_method": "", "category": "infrastructure",
                     "harm": "runtime", "detail": {}, "extracted_at": now})
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_loop_full_closure_error_to_attestation_to_route(loop, loop_home):
    """①L1错误 → ②L2锚点 → ③L3确权(ERGE+资产) → 反哺L1 全链可验证。"""
    # ① L1→L2: 同类错误×2 → 自动升级锚点 + 冲突即时避让
    loop.record_route_result("novarouteai", "deepseek-v4-flash",
                             False, "HTTP 403 forbidden")
    loop.record_route_result("novarouteai", "deepseek-v4-flash",
                             False, "HTTP 403 forbidden")
    assert loop.bus.stats()["promoted_anchors"] == 1
    auto = [a for a in loop.anchor_store.lookup()
            if a["anchor_id"].startswith("route-error-")]
    assert len(auto) == 1

    # ② L2→L3: 未授权 → 只报 awaiting_consent·不确权
    r0 = loop.confirm_experiences(authorized=False)
    assert auto[0]["anchor_id"] in r0["awaiting_consent"]
    assert r0["confirmed"] == []

    # ③ 授权 → 契约+存证+ERGE五表+资产
    r1 = loop.confirm_experiences(authorized=True)
    assert len(r1["confirmed"]) == 1
    c = r1["confirmed"][0]
    assert c["attestation"] and c["asset_id"]
    conn = sqlite3.connect(loop.erge_db)
    try:
        n_anchors = conn.execute("SELECT COUNT(*) FROM anchors").fetchone()[0]
        n_perms = conn.execute("SELECT COUNT(*) FROM permissions").fetchone()[0]
        n_vers = conn.execute("SELECT COUNT(*) FROM versions").fetchone()[0]
    finally:
        conn.close()
    assert n_anchors >= 1 and n_perms >= 1 and n_vers >= 1
    assert loop.assets.verify(c["asset_id"])

    # ④ L3→L1: 确权后的约束已反哺 route()(避开问题 provider)
    router = ModelRouter()
    loop.attach_router(router)
    sel = router.route("light", task_text="模板回复")
    assert sel.provider != "novarouteai"

    # ⑤ 持久化: 重启(loop 重建)后约束仍在(错误复利跨进程)
    loop2 = ExperienceLoop(home=loop_home)
    assert any("novarouteai" in str(c.get("provider_avoid", []))
               for c in loop2.bus.active_route_constraints())


def test_loop_ris_recovery_feedback(loop, loop_home):
    """RIS 恢复经验反哺: 成功→免疫标记+锚点; 失败→错误复利。"""
    ris_path = os.path.join(loop_home, "ris.jsonl")
    _ris_store(ris_path, n_success=3, n_fail=2)
    r = loop.ingest_ris_recovery(store_path=ris_path)
    assert r["ok"]
    assert r["immune_marked"] == 3                    # 系统免疫
    assert r["failure_patterns"] == 2                 # 失败进错误复利
    assert loop.bus.is_immune("ris", "any", "ris_anomaly")  # 跨类共享免疫
    assert r["anchors_added"] >= 1                    # 成功经验≥3 → Fact锚点
