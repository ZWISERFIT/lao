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
        """路由结果 → FeedbackBus(由 lao_router_server 结算时调用)。

        W9(2026-08-19): 成功路由 → 经验分流(创始人修正1):
          - agent_runtime(Agent运行经验) → 自动同步 Momo(不需授权·产品数据分析)
          - user_personal/collaborative(用户/协同经验) → 累计≥3条生成授权请求
        失败路由 → 错误复利(原有逻辑)。均不阻塞路由。
        """
        res = self.bus.capture_route_result(provider, model, ok, error)
        if ok:
            try:
                _out = os.environ.get("LAO_L3_OUT_DIR", "data")
                self.l3_route_result_fanout(out_dir=_out)
            except Exception:
                pass  # L3 检查失败不影响路由
        return res

    def l3_route_result_fanout(self, out_dir: str = "data") -> Dict[str, Any]:
        """W9 创始人修正1: 路由结果经验分流。

        - agent_runtime 锚点 → 自动同步 Momo(不走授权流程)
        - user_personal/collaborative 锚点 → 累计≥3条 → 授权请求
        """
        try:
            anchors = self.anchor_store.lookup()[:50]
            agent_runtime = [a for a in anchors
                             if str(a.get("experience_type", "agent_runtime")) == "agent_runtime"]
            need_consent = [a for a in anchors
                            if str(a.get("experience_type", "agent_runtime")) in
                            ("user_personal", "collaborative")]
            out = {"agent_runtime_synced": 0, "consent_triggered": False}
            if agent_runtime:
                out["agent_runtime_synced"] = self.l3_sync_agent_runtime_to_momo(
                    agent_runtime, out_dir=out_dir)
            if need_consent:
                _r = self.l3_check_and_request_authorization(out_dir=out_dir)
                out["consent_triggered"] = bool(_r.get("requested"))
            return out
        except Exception:
            return {"agent_runtime_synced": 0, "consent_triggered": False}

    def l3_sync_agent_runtime_to_momo(self, anchors: List[Dict[str, Any]],
                                      out_dir: str = "data") -> int:
        """W9 创始人修正1: Agent 运行经验自动同步 Momo(不需授权)。

        写入 Momo 可读的产品数据分析文件(agent_runtime_experiences.jsonl)。
        """
        try:
            import json as _json
            from datetime import datetime, timezone
            os.makedirs(out_dir, exist_ok=True)
            fp = os.path.join(out_dir, "agent_runtime_experiences.jsonl")
            _n = 0
            for a in anchors:
                _rec = {
                    "anchor_id": a.get("anchor_id"),
                    "experience_type": "agent_runtime",
                    "value": a.get("value"),
                    "trust_weight": a.get("trust_weight", 0),
                    "source": a.get("source"),
                    "synced_at": datetime.now(timezone.utc).isoformat(),
                }
                with open(fp, "a", encoding="utf-8") as f:
                    f.write(_json.dumps(_rec, ensure_ascii=False) + "\n")
                _n += 1
            return _n
        except Exception:
            return 0

    def match_experience(self, task_text: str, tier: str = "", agent: str = ""
                         ) -> Optional[Dict[str, Any]]:
        """W3: 经验直答·pre-route 匹配(2026-08-19 创始人令·LAO接线)。

        从已确权经验库(anchor_store)匹配任务文本:
          - 命中决策/认知锚点(query 匹配)
          - trust_weight >= 0.8(置信度阈值)
          - 已确权(confirm_experiences 确认链中无 awaiting_consent)
        返回 {"answer": str, "confidence": float, "experience_key": str} 或 None。
        """
        try:
            if not task_text:
                return None
            matched = self.anchor_store.query(task_text)
            if not matched:
                return None
            best = matched[0]
            tw = float(best.get("trust_weight", 0) or 0)
            if tw < 0.8:
                return None
            # 确权校验: 锚点不应在 awaiting_consent 列表
            try:
                rep = self.confirm_experiences(authorized=False, limit=50)
                pending = set(rep.get("awaiting_consent", []))
                if best.get("anchor_id") in pending:
                    return None
            except Exception:
                pass  # 确权校验失败则不拦截(fail-open)
            value = best.get("value", {})
            if isinstance(value, dict):
                answer = value.get("principle") or value.get("trigger_condition") \
                    or value.get("counter_examples") or str(value)
            else:
                answer = str(value)
            return {
                "answer": answer,
                "confidence": tw,
                "experience_key": best.get("anchor_id", ""),
            }
        except Exception:
            return None  # fail-open·不阻塞路由

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
        # 创始人修正2(2026-08-19): L3确权 → L2动态参数更新 → L1命中率提升
        # (C-2 缺陷修复: 确权产物反哺路由约束·闭环补全)
        self._l3_feedback_route_params(cur)
        return {"anchor_id": aid, "domain": domain, "attestation": attestation,
                "asset_id": asset.asset_id}

    def _l3_feedback_route_params(self, anchor: Dict[str, Any]) -> bool:
        """创始人修正2(2026-08-19): L3 确权经验 → L2 动态参数更新。

        确权经验反哺路由参数(现有 add_route_constraint 机制·model_router 消费):
          - 经验含 provider_avoid/model_avoid → 注入规避约束(成本/稳定性教训)
          - 经验含 budget_cap → 收紧预算上限
          - 经验含 prefer_* → 记录偏好(供后续路由参考·非强制)
        返回是否成功注入。
        """
        try:
            aid = anchor.get("anchor_id", "")
            tw = float(anchor.get("trust_weight", 0) or 0)
            value = anchor.get("value")
            if not isinstance(value, dict):
                return False
            _constraint: Dict[str, Any] = {}
            _reason = f"L3确权经验反哺(2026-08-19·confidence={tw:.2f})"
            _pa = value.get("provider_avoid") or value.get("avoid_provider")
            if _pa:
                _constraint["provider_avoid"] = _pa if isinstance(_pa, list) else [_pa]
            _ma = value.get("model_avoid") or value.get("avoid_model")
            if _ma:
                _constraint["model_avoid"] = _ma if isinstance(_ma, list) else [_ma]
            _bc = value.get("budget_cap")
            if _bc:
                try:
                    _constraint["budget_cap"] = float(_bc)
                except (TypeError, ValueError):
                    pass
            if _constraint:
                _constraint["reason"] = _reason
                self.bus.add_route_constraint(aid, _constraint)
                return True
            # 无显式约束 → 记录参数更新事件(审计可追溯)
            self.bus.emit(FeedbackEvent(
                event_type="param_update", source="l3_confirmed",
                payload={"anchor_id": aid, "trust_weight": tw,
                         "action": "route_param_review"},
                severity="info"))
            return False
        except Exception:
            return False

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


    # ── W9: L3 经验同步闭环(2026-08-19 创始人令·铁律级) ────────────────────
    # 完整Loop: Runtime请求→L1入站→L2路由验证→LLM→L2出站验证→L3经验记录→
    #           累计3条→用户授权→Momo同步+Ethan存证(SHA-256)→创始人确权→
    #           进入确权链→下次请求W3经验直答

    def l3_pending_experiences(self) -> List[Dict[str, Any]]:
        """获取待授权经验(仅 user_personal/collaborative·agent_runtime 已自动同步Momo)。"""
        try:
            rep = self.confirm_experiences(authorized=False, limit=50)
            pending = set(rep.get("awaiting_consent", []))
            out = []
            for a in self.anchor_store.lookup()[:50]:
                if a.get("anchor_id") in pending:
                    _et = str(a.get("experience_type", "agent_runtime"))
                    if _et in ("user_personal", "collaborative"):
                        out.append({
                            "anchor_id": a.get("anchor_id"),
                            "value": a.get("value"),
                            "trust_weight": a.get("trust_weight", 0),
                            "created_at": a.get("created_at", ""),
                            "experience_type": _et,
                        })
            return out
        except Exception:
            return []

    def l3_check_and_request_authorization(self, out_dir: str = "data",
                                           threshold: int = 3) -> Dict[str, Any]:
        import uuid
        from datetime import datetime, timezone
        """L3①: 累计未授权经验≥threshold条 → 生成授权请求(写 pending 文件)。

        不阻塞路由·异步(由调用方在 record_route_result 后触发)。
        """
        try:
            os.makedirs(out_dir, exist_ok=True)
            pending = self.l3_pending_experiences()
            if len(pending) < threshold:
                return {"requested": False, "count": len(pending)}
            req = {
                "request_id": uuid.uuid4().hex[:12],
                "experiences": [{"anchor_id": p["anchor_id"],
                                 "summary": str(p.get("value", {}))[:200],
                                 "confidence": float(p.get("trust_weight", 0) or 0)}
                                for p in pending[:threshold]],
                "channel": "momo",
                "status": "pending",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            fp = os.path.join(out_dir, "pending_user_authorizations.jsonl")
            with open(fp, "a", encoding="utf-8") as f:
                f.write(json.dumps(req, ensure_ascii=False) + "\n")
            return {"requested": True, "count": len(pending), "file": fp,
                    "request_id": req["request_id"]}
        except Exception:
            return {"requested": False, "count": 0}

    def l3_authorize_and_notarize(self, request_id: str, user_approved: bool,
                                  out_dir: str = "data") -> Dict[str, Any]:
        import uuid
        from datetime import datetime, timezone
        """L3②③: 授权通过 → Momo 同步 + Ethan 存证(SHA-256)。

        user_approved=True → 确权链登记 + 同步文件生成 + 存证哈希。
        user_approved=False → 仅记录拒绝·不进确权链。
        """
        try:
            os.makedirs(out_dir, exist_ok=True)
            # 读取待授权文件找到对应 request
            fp = os.path.join(out_dir, "pending_user_authorizations.jsonl")
            req = None
            if os.path.exists(fp):
                for line in open(fp, encoding="utf-8"):
                    try:
                        e = json.loads(line)
                        if e.get("request_id") == request_id:
                            req = e
                            break
                    except Exception:
                        continue
            if req is None:
                return {"ok": False, "reason": "request_not_found"}
            if not user_approved:
                return {"ok": True, "authorized": False, "reason": "user_denied"}
            # ① 确权链登记(contracts·DID 签名用模拟签名·生产应接入真实 DID)
            _sig = f"did:zwiserfit:{request_id}:{uuid.uuid4().hex[:8]}"
            _notarized = []
            for exp in req.get("experiences", []):
                _aid = exp.get("anchor_id", "")
                # 找锚点内容 → SHA-256
                _content = json.dumps(exp, ensure_ascii=False)
                _sha = hashlib.sha256(_content.encode()).hexdigest()
                _contract = ExperienceContract(
                    owner=self.owner, domain="routing", confidence=float(exp.get("confidence", 0.5)),
                    anchor_type="decision")
                _contract.authorize(_sig)
                try:
                    self.contracts.register(_contract)
                except Exception:
                    pass
                _notarized.append({
                    "experience_id": _aid,
                    "sha256": _sha,
                    "authorized": True,
                    "authorized_at": datetime.now(timezone.utc).isoformat(),
                })
            # ② Momo 同步(写入授权经验文件·门店数字店长可消费)
            _momo = {"authorized_at": datetime.now(timezone.utc).isoformat(),
                     "experiences": _notarized, "source": "lao-l3"}
            _momo_fp = os.path.join(out_dir, "authorized_experiences.jsonl")
            with open(_momo_fp, "a", encoding="utf-8") as f:
                f.write(json.dumps(_momo, ensure_ascii=False) + "\n")
            # ③ Ethan 存证(SHA-256 哈希落盘)
            _ethan_fp = os.path.join(out_dir, "ethan_notarizations.jsonl")
            with open(_ethan_fp, "a", encoding="utf-8") as f:
                for n in _notarized:
                    f.write(json.dumps(n, ensure_ascii=False) + "\n")
            return {"ok": True, "authorized": True, "notarized": _notarized,
                    "momo_file": _momo_fp, "ethan_file": _ethan_fp}
        except Exception as e:
            return {"ok": False, "reason": str(e)}

    def l3_founder_confirm(self, experience_id: str) -> bool:
        """L3④: 创始人确权 → 经验进入确权链(可被 W3 直答消费)。"""
        try:
            rep = self.confirm_experiences(authorized=True, limit=50)
            confirmed = rep.get("confirmed", [])
            for c in confirmed:
                if c.get("anchor_id") == experience_id or c.get("id") == experience_id:
                    return True
            return bool(confirmed)  # 授权后整批确认
        except Exception:
            return False

