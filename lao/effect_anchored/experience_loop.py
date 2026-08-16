"""
ExperienceLoop — LAO v3.4 三层联动闭环 (创始人令 2026-08-16)
==============================================================

    L1(命中率) ←─ 反哺 ─ L3(确权经验) ←─ L2(经验工厂产锚点)
            ↘              ↗
             Agent运营经验(反哺L1命中率+RIS免疫)

审计结论(2026-08-16): L2 锚点/L3 确权的接口全部写好但零接线 —
FeedbackBus 无发布者无订阅者无持久化、ErgeWriter/attest_experience/
readiness 全部零调用。本模块是那个"接线员":

  ① L1→L2: record_route_result — 路由结果回流 FeedbackBus
            (错误×N 自动升级锚点 + provider 避让约束·冲突即时避让)
  ② L2→L3: confirm_experiences — 锚点 → 幻觉门校验 → readiness 量达标
            → 用户授权 → 契约注册 → Ethan 存证(离线降级本地指纹)
            → ErgeWriter 五表落库 → ExperienceAsset 资产确权
  ③ L3→L1: 确权产物 → 路由约束(bus.add_route_constraint →
            ModelRouter.with_feedback_bus 反哺命中率/稳定性)
  ④ RIS→Loop: ingest_ris_recovery — RIS 恢复经验 → 锚点 + 系统免疫标记
            (失败模式进错误复利·成功模式进经验复利)

所有子组件 fail-open: 任何一层故障不阻塞其余层(路由永远可用)。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from typing import Any, Dict, List, Optional

from lao.effect_anchored.cognitive_anchor import (
    CognitiveAnchorStore,
)
from lao.effect_anchored.consent_gate import ConsentGate, FourStageConsent
from lao.effect_anchored.experience_asset import ExperienceAssetRegistry
from lao.effect_anchored.experience_contract import (
    ExperienceContract,
    ExperienceContractRegistry,
)
from lao.effect_anchored.experience_readiness import (
    ExperienceReadinessTracker,
    ReadinessConfig,
)
from lao.effect_anchored.feedback_bus import FeedbackBus, FeedbackEvent
from lao.effect_anchored.hallucination_gate import HallucinationGate

DEFAULT_HOME = os.environ.get(
    "LAO_LOOP_HOME",
    os.path.join(os.path.expanduser("~"), ".lao", "experience-loop"),
)

# 确权前幻觉门校验的锚点结构 schema(缺字段/类型错 = 不确权·减少幻觉)
ANCHOR_SCHEMA = {
    "type": "object",
    "required": ["anchor_id", "anchor_type", "value"],
    "properties": {
        "anchor_id": {"type": "string", "minLength": 1},
        "anchor_type": {"type": "string",
                        "enum": ["fact", "decision", "cognitive"]},
        "value": {},
        "trust_weight": {"type": "number", "minimum": 0, "maximum": 1},
    },
}

# ERGE 五表 DDL(与生产 anchors.db 同构·loop 私有库不存在时自建)
_ERGE_DDL = [
    """CREATE TABLE IF NOT EXISTS anchors (
        id TEXT PRIMARY KEY,
        category TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'candidate',
        owner TEXT NOT NULL DEFAULT 'suzanne',
        scope TEXT NOT NULL DEFAULT 'global',
        trust_weight REAL NOT NULL DEFAULT 0.5,
        impact_level TEXT NOT NULL DEFAULT 'decision_aid',
        confidence_score REAL NOT NULL DEFAULT 0.5,
        evidence_count INTEGER NOT NULL DEFAULT 1,
        last_verified TEXT,
        source_type TEXT NOT NULL DEFAULT 'agent_derived',
        source_event_id TEXT,
        source_timestamp TEXT NOT NULL DEFAULT (datetime('now')),
        rule TEXT NOT NULL DEFAULT '',
        rationale TEXT,
        counter_example TEXT,
        preference_firewall INTEGER NOT NULL DEFAULT 0,
        supersedes TEXT,
        version INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        anchor_type TEXT CHECK(anchor_type IN ('fact','decision','cognitive')))""",
    """CREATE TABLE IF NOT EXISTS tags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        anchor_id TEXT NOT NULL, tag TEXT NOT NULL,
        FOREIGN KEY (anchor_id) REFERENCES anchors(id) ON DELETE CASCADE,
        UNIQUE(anchor_id, tag))""",
    """CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        anchor_id TEXT NOT NULL, event_type TEXT NOT NULL,
        from_status TEXT, to_status TEXT, confidence_delta REAL,
        agent TEXT, reason TEXT,
        timestamp TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (anchor_id) REFERENCES anchors(id) ON DELETE CASCADE)""",
    """CREATE TABLE IF NOT EXISTS versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        anchor_id TEXT NOT NULL, version INTEGER NOT NULL,
        snapshot TEXT NOT NULL, created_by TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (anchor_id) REFERENCES anchors(id) ON DELETE CASCADE,
        UNIQUE(anchor_id, version))""",
    """CREATE TABLE IF NOT EXISTS permissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        anchor_id TEXT NOT NULL, agent_id TEXT NOT NULL,
        access_level TEXT NOT NULL DEFAULT 'read',
        FOREIGN KEY (anchor_id) REFERENCES anchors(id) ON DELETE CASCADE,
        UNIQUE(anchor_id, agent_id))""",
]


class ExperienceLoop:
    """三层联动闭环编排器(L1↔L2↔L3 + RIS)。"""

    def __init__(self, home: Optional[str] = None, owner: str = "lao",
                 readiness_config: Optional[ReadinessConfig] = None):
        self.home = home or DEFAULT_HOME
        os.makedirs(self.home, exist_ok=True)
        self.owner = owner

        # L2 经验工厂(全部持久化·缓存空间收敛见各组件)
        self.anchor_store = CognitiveAnchorStore(
            os.path.join(self.home, "anchors.json"))
        self.bus = FeedbackBus(state_path=os.path.join(self.home, "feedback.json"))
        self.bus.configure_auto_promote(self.anchor_store)
        self.hgate = HallucinationGate(
            violation_log_path=os.path.join(self.home, "hgate_violations.jsonl"))

        # L3 确权交易
        self.consent = FourStageConsent(os.path.join(self.home, "consent.json"))
        self.consent_gate = ConsentGate(os.path.join(self.home, "consent_gate.json"))
        self.contracts = ExperienceContractRegistry(
            os.path.join(self.home, "contracts.json"),
            consent_gate=self.consent_gate)
        self.assets = ExperienceAssetRegistry(
            os.path.join(self.home, "assets.json"))
        self.readiness = ExperienceReadinessTracker(
            readiness_config or ReadinessConfig())

        # ERGE 落库(默认 loop 私有库·生产可用 LAO_LOOP_ERGE_DB 指向真库)
        from lao.effect_anchored.erge_writer import ErgeWriter
        self.erge_db = os.environ.get(
            "LAO_LOOP_ERGE_DB", os.path.join(self.home, "anchors.db"))
        self._ensure_erge_schema()
        self.erge = ErgeWriter(db_path=self.erge_db)

    # ── ① L1→L2: 路由结果回流(错误复利/冲突修正/经验复利) ────────────────

    def record_route_result(self, provider: str, model: str, ok: bool,
                            error: str = ""):
        """路由结果 → FeedbackBus(由 lao_router_server 结算时调用)。"""
        return self.bus.capture_route_result(provider, model, ok, error)

    def attach_router(self, router) -> "ExperienceLoop":
        """把总线挂进 ModelRouter(L2/L3 约束 → L1 路由反哺)。"""
        router.with_feedback_bus(self.bus)
        return self

    # ── ② L2→L3: 确权(锚点→幻觉门→readiness→授权→契约→存证→落库→资产) ──

    def anchor_readiness_meta(self, cur: Dict[str, Any]) -> Dict[str, Any]:
        """锚点 → readiness 元数据(触发次数/跨域/年龄/置信度)。"""
        value = cur.get("value") if isinstance(cur.get("value"), dict) else {}
        weight_hits = float(self.bus.cognitive._weights.get(cur.get("anchor_id", ""), 0.0))
        correction = float(value.get("correction_count", 0) or 0)
        trigger_count = int(max(correction, weight_hits, value.get("evidence_count", 0) or 0))
        tags = cur.get("tags", []) or []
        # 跨域: 标签覆盖 >1 个语义域(routing/experience/ris/...) 视为跨域复用
        domains = {t.split(":")[0] for t in tags if isinstance(t, str)}
        cross_domain = 1 if (len(set(tags)) > 1 or len(domains) > 1
                             or "auto-promoted" in tags) else 0
        return {
            "id": cur.get("anchor_id", ""),
            "trigger_count": trigger_count,
            "cross_domain": cross_domain,
            "created_at": cur.get("created_at", ""),
            "confidence": float(cur.get("trust_weight", 0) or 0),
        }

    def confirm_experiences(self, authorized: bool = False, limit: int = 20,
                            owner: Optional[str] = None) -> Dict[str, Any]:
        """确权主流程(L2→L3→L1)。

        authorized=False: 只跑本地量判定+幻觉门, 返回 awaiting_consent 清单
        (授权必须用户显式给 — 创始人令"经用户授权确权交易")。
        authorized=True: owner 视为已授予③上传+④交易 → 全链确权。
        """
        owner = owner or self.owner
        anchors = self.anchor_store.lookup()[:limit]
        meta_list = [self.anchor_readiness_meta(a) for a in anchors]
        ready_results = self.readiness.ready_batch(meta_list, enforce_consent=False)
        ready_ids = {r.experience_id for r in ready_results}

        report: Dict[str, Any] = {
            "owner": owner, "authorized": authorized,
            "total_anchors": len(anchors), "ready": len(ready_ids),
            "gate_failed": [], "confirmed": [], "awaiting_consent": [],
        }
        by_id = {a.get("anchor_id"): a for a in anchors}
        for aid in ready_ids:
            cur = by_id.get(aid)
            if cur is None:
                continue
            # 幻觉门: 结构完整性校验(缺字段/类型错 → 拒绝确权·减少幻觉)
            h = self.hgate.check(cur, expected_schema=ANCHOR_SCHEMA)
            if not h.passed:
                report["gate_failed"].append(
                    {"anchor_id": aid, "reason": getattr(h, "reason", "schema")})
                continue
            if not authorized:
                report["awaiting_consent"].append(aid)
                continue
            report["confirmed"].append(self._confirm_one(cur, owner))
        return report

    def _confirm_one(self, cur: Dict[str, Any], owner: str) -> Dict[str, Any]:
        """单锚点确权: 契约→授权→存证→ERGE→资产→(约束反哺已就位)。"""
        aid = cur.get("anchor_id", "")
        anchor_type = cur.get("anchor_type", "fact")
        domain = f"experience/{anchor_type}"
        # 契约注册(确权的法律层)
        self.contracts.register(ExperienceContract(
            owner=owner, domain=domain,
            allowed_agents=["lao"], forbidden_domains=[],
            confidence=float(cur.get("trust_weight", 0.5) or 0.5),
            source=cur.get("source"), anchor_type=anchor_type))
        # 用户授权(authorized=True 语义: 四阶段③④ + ConsentGate 全项)
        for stage in ("upload", "trade"):
            self.consent.grant_stage(stage, owner, domain)
        self.consent_gate.grant(owner, domain, {i["id"]: True for i in
                                                self._consent_items()})
        # Ethan 存证(离线 → 降级为本地 sha256 指纹·诚实记录)
        attestation = None
        try:
            attestation = self.contracts.attest_experience(owner, domain)
        except Exception:
            attestation = None
        if not attestation:
            payload = json.dumps(cur, ensure_ascii=False, sort_keys=True)
            attestation = "local:sha256:" + hashlib.sha256(
                payload.encode()).hexdigest()[:32]
        # ERGE 五表落库(可检索复用层)
        self.erge.write_anchor(cur, agent=owner,
                               reason="v3.4 三层Loop确权(2026-08-16)")
        # ExperienceAsset 资产化(交易层)
        value = cur.get("value") if isinstance(cur.get("value"), dict) else {}
        asset = self.assets.create(
            creator_did=f"did:zwf:{owner}",
            problem=str(value.get("trigger_condition") or aid),
            solution=str(value.get("action_rule") or value.get("principle") or aid),
            domain=anchor_type,
            verification_pct=round(float(cur.get("trust_weight", 0) or 0) * 100, 2),
            trust_event_hash=attestation if attestation.startswith("sha256:") else "",
            tags=list(cur.get("tags", [])))
        return {"anchor_id": aid, "domain": domain, "attestation": attestation,
                "asset_id": asset.asset_id}

    @staticmethod
    def _consent_items() -> List[Dict[str, Any]]:
        from lao.effect_anchored.consent_gate import CONSENT_ITEMS
        return CONSENT_ITEMS

    # ── ④ RIS→Loop: 恢复经验反哺(系统免疫) ────────────────────────────────

    def ingest_ris_recovery(self, store_path: Optional[str] = None) -> Dict[str, Any]:
        """RIS 恢复经验 → L2 锚点(成功复利) + 错误复利(失败模式) + 免疫标记。"""
        try:
            from ris.experience.risk_experience_extractor import RiskExperienceExtractor
        except Exception:
            return {"ok": False, "reason": "ris.experience 不可用"}
        try:
            extractor = RiskExperienceExtractor(store_path=store_path) \
                if store_path else RiskExperienceExtractor()
        except Exception as e:
            return {"ok": False, "reason": f"extractor 初始化失败: {e}"}

        result = {"ok": True, "anchors_added": 0, "immune_marked": 0,
                  "failure_patterns": 0}
        # 成功恢复经验 → Fact 锚点(经验复利·供检索/确权)
        try:
            for anchor in extractor.to_lao_anchors():
                self.anchor_store.put(anchor)
                result["anchors_added"] += 1
        except Exception:
            pass
        # 台账统计 → 错误复利(失败模式) + 系统免疫标记(成功已验证)
        try:
            stats = extractor.stats()
            for exp in extractor._load_store():
                if exp.get("recovered") and exp.get("verified"):
                    # 免疫: 该异常类型已有验证过的恢复路径(RIS 系统免疫)
                    self.bus.mark_immune("ris", str(exp.get("event_type", "")),
                                         "ris_anomaly")
                    result["immune_marked"] += 1
                elif not exp.get("recovered"):
                    self.bus.emit(FeedbackEvent(
                        event_type="error", source="ris_recovery",
                        payload={"error_signature":
                                 f"ris:{exp.get('classified', 'unknown')} not recovered"}))
                    result["failure_patterns"] += 1
            result["ris_stats"] = stats
        except Exception:
            pass
        return result

    # ── 运维 ─────────────────────────────────────────────────────────────

    def _ensure_erge_schema(self) -> None:
        conn = sqlite3.connect(self.erge_db)
        try:
            for ddl in _ERGE_DDL:
                conn.execute(ddl)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_permissions_agent "
                         "ON permissions(agent_id)")
            conn.commit()
        finally:
            conn.close()

    def status(self) -> Dict[str, Any]:
        """三层状态总览(可观测·Dashboard/审计消费)。"""
        try:
            erge_stats = self.erge.stats()
        except Exception:
            erge_stats = {}
        return {
            "home": self.home,
            "l2": {"anchors": self.anchor_store.stats(),
                   "bus": self.bus.stats()},
            "l3": {"assets": self.assets.count(),
                   "erge": erge_stats,
                   "consent": self.consent.stage_status(self.owner, "experience/decision")},
            "gate_violations": len(self.hgate.violations()),
        }
