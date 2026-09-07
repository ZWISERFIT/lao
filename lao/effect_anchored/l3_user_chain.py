# -*- coding: utf-8 -*-
"""l3_user_chain · 223号施工令 #34：LAO L3 授权确权交易链（用户/协同经验半）

链路（8-25 裁定2「L3 切两半」的用户经验半）：
    Qoder 会话人机协同经验 → human 类打标入库（anchors.json + raw_trace 原卷账）
    → 同一 owner+同一 domain 累计 10 条 → 用户授权（FOUR_STAGES upload/trade）
    → Ethan 确权打标（attestation + 权属登记）→ 上架件 → Zeus 管理交易

口径依据：
    - 213号需求书 §B3 / 191号 #34 / 8-25 三项裁定
    - 223号施工令签章口径：阈值 10 条，同一 owner + 同一 domain，错误类与成功类都计入
    - 交易主体 = Zeus（Melody 市场降为会员侧口径，不作交易主体）

治理边界（192号默认关闭宪法 + PIPL）：
    1. 上架投递默认关闭：未显式开启 LAO_L3_TRADE_ENABLED=1 一律不投递 Zeus；
    2. 未授权必阻断：确权走 upload 阶段闸门、上架走 trade 阶段闸门；
    3. 原始经验仅存本地：投递 Zeus 的上架件只含哈希化元数据，不含经验原文；
    4. 不放宽 120号件既有授权范围（scope=founder_collaboration_trace / 365天）。
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ── 经验类型（与 experience_loop / experience_classifier 口径一致）────────────
TYPE_USER_PERSONAL = "user_personal"
TYPE_COLLABORATIVE = "collaborative"
HUMAN_TYPES = (TYPE_USER_PERSONAL, TYPE_COLLABORATIVE)

# ── 223号签章口径 ────────────────────────────────────────────────────────────
AUTH_THRESHOLD = 10          # 同一 owner + 同一 domain 累计 10 条（错误类+成功类都计入）
CONSENT_DOMAIN_PREFIX = "l3-user/"
REVENUE_FORMULA = "user_50_platform_50"
LISTING_SCHEMA = "l3-trade-listing/1.0"
TRADE_FLAG = "LAO_L3_TRADE_ENABLED"   # 默认关闭：仅显式置 1 时才向 Zeus 投递

DEFAULT_LOOP_HOME = os.path.expanduser("~/.lao/experience-loop")
ZEUS_INPUTS = os.path.expanduser("~/ral-store/units/market/zeus/inputs")
ETHAN_INPUTS = os.path.expanduser("~/ral-store/units/finance/ethan/inputs")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _flag_on(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in ("1", "true", "yes", "on")


def _loop_home(home: Optional[str] = None) -> str:
    return home or os.environ.get("LAO_LOOP_HOME") or DEFAULT_LOOP_HOME


def _vault_root(vault_root: Optional[str] = None) -> str:
    """原卷账根目录：与 120号件既有授权目录一致（默认 ~/raw_trace）。"""
    return (vault_root or os.environ.get("LAO_RAW_TRACE_ROOT")
            or os.path.join(os.environ.get("LAO_HOME", os.path.expanduser("~")),
                            "raw_trace"))


def _data_dir(home: Optional[str] = None) -> str:
    d = os.path.join(_loop_home(home), "data")
    os.makedirs(d, exist_ok=True)
    return d


def consent_domain(domain: str) -> str:
    """授权域键：与 FourStageConsent 的 owner:domain 键位对齐。"""
    return CONSENT_DOMAIN_PREFIX + str(domain)


def _anchor_store(home: Optional[str] = None):
    from lao.effect_anchored.cognitive_anchor import CognitiveAnchorStore
    return CognitiveAnchorStore(os.path.join(_loop_home(home), "anchors.json"))


def _four_stage(home: Optional[str] = None):
    from lao.effect_anchored.consent_gate import FourStageConsent
    return FourStageConsent(os.path.join(_loop_home(home), "consent.json"))


def _append_jsonl(path: str, record: Dict[str, Any]) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def make_experience_id(owner: str, domain: str, summary: str) -> str:
    """经验 id：掺入 uuid4，确保同一时刻连写多条不会撞 id。

    撞 id 会被 CognitiveAnchorStore.put 当成版本更新（旧值入 history），
    导致条数少计、阈值判定偏低，故不可只靠时间戳去重。
    """
    raw = "%s|%s|%s|%s|%s" % (owner, domain, summary, _now_iso(), uuid.uuid4())
    return "ux-" + _sha256(raw)[:12]


# ── P1 · human 类经验打标入库（并用 raw_trace 原卷账）──────────────────────────

def record_user_experience(owner: str, domain: str, summary: str,
                           outcome: str = "success",
                           experience_type: str = TYPE_COLLABORATIVE,
                           source: str = "qoder_session",
                           trust_weight: float = 0.8,
                           tags: Optional[List[str]] = None,
                           home: Optional[str] = None,
                           vault_root: Optional[str] = None,
                           raw_trace: bool = True,
                           anchor_id: Optional[str] = None) -> Dict[str, Any]:
    """把一条人机协同/用户个人经验打 human 类标并入库。

    - anchors.json：experience_type ∈ (user_personal, collaborative)，
      agent_runtime 既有锚点零改动（本函数只新增，不覆写他人锚点）；
    - raw_trace：授权有效时并写原卷账 + 引用登记（manifest 沿用
      ral-raw-trace-manifest/1.0）；无有效授权则静默跳过（默认关闭语义）。
    """
    if experience_type not in HUMAN_TYPES:
        raise ValueError("experience_type 仅接受 %s" % (HUMAN_TYPES,))
    if outcome not in ("success", "error"):
        raise ValueError("outcome 仅接受 success/error（错误类与成功类都计入阈值）")

    from lao.effect_anchored.cognitive_anchor import make_fact_anchor

    aid = anchor_id or make_experience_id(owner, domain, summary)
    value = {
        "trigger_condition": str(summary)[:400],
        "action_rule": str(summary)[:400],
        "outcome": outcome,
        "domain": str(domain),
        "owner": str(owner),
        "recorded_at": _now_iso(),
    }
    anchor = make_fact_anchor(
        aid, value, source=source, trust_weight=float(trust_weight),
        tags=list(tags or []) + ["l3-user-chain", "domain:%s" % domain,
                                 "outcome:%s" % outcome],
        experience_type=experience_type)
    store = _anchor_store(home)
    anchor_hash = store.put(anchor)

    out = {"anchor_id": aid, "experience_type": experience_type,
           "domain": str(domain), "owner": str(owner),
           "anchor_hash": anchor_hash, "outcome": outcome,
           "raw_trace": {"logged": False, "registered": False,
                         "reason": "disabled"}}
    if raw_trace:
        out["raw_trace"] = _raw_trace_dual_write(aid, owner, domain,
                                                 experience_type, value,
                                                 vault_root)
    return out


def _raw_trace_dual_write(anchor_id: str, owner: str, domain: str,
                          experience_type: str, value: Dict[str, Any],
                          vault_root: Optional[str] = None) -> Dict[str, Any]:
    """原卷账 + 引用登记并写。无有效授权即静默跳过，异常不上抛。"""
    result = {"logged": False, "registered": False, "reason": ""}
    try:
        from lao.effect_anchored.raw_trace.redaction import redact_payload
        from lao.effect_anchored.raw_trace.registry import ReferenceRegistry
        from lao.effect_anchored.raw_trace.vault import RawTraceVault

        root = _vault_root(vault_root)
        vault = RawTraceVault(root)
        if vault.consent.active() is None:
            result["reason"] = "no_consent"      # 默认关闭：零写入
            return result

        payload = {"anchor_id": anchor_id, "owner": owner, "domain": domain,
                   "experience_type": experience_type, "value": value}
        vault.append(kind="l3_user_experience", payload=payload,
                     source="l3_user_chain.record_user_experience")
        result["logged"] = True

        # 经验卡对象文件（registry 只登记指向与哈希，不复制正文）
        cards_dir = os.path.join(root, "cards")
        os.makedirs(cards_dir, exist_ok=True)
        card_file = os.path.join(cards_dir, "%s.json" % anchor_id)
        with open(card_file, "w", encoding="utf-8") as f:
            json.dump(redact_payload(payload), f, ensure_ascii=False,
                      indent=2, sort_keys=True)

        library = "personal" if experience_type == TYPE_USER_PERSONAL else "collaborative"
        ReferenceRegistry(root).register(
            card_id=anchor_id, version=1, subject_id="did:zwf:%s" % owner,
            library=library, ownership=owner, visibility="internal",
            origin_path=card_file, revenue_formula=REVENUE_FORMULA,
            actor=owner)
        result["registered"] = True
    except Exception as e:                       # 守护：不破坏打标主流程
        result["reason"] = "%s: %s" % (type(e).__name__, e)
    return result


# ── P2 · 10 条阈值器（同 owner + 同 domain，错误类+成功类都计入）───────────────

def human_anchors(owner: Optional[str] = None, home: Optional[str] = None,
                  confirmed: Optional[bool] = None) -> List[Dict[str, Any]]:
    """列出 human 类（user_personal / collaborative）锚点。

    confirmed=None 全部；True 仅已确权；False 仅未确权。
    """
    out: List[Dict[str, Any]] = []
    for a in _anchor_store(home).lookup():
        if str(a.get("experience_type", "agent_runtime")) not in HUMAN_TYPES:
            continue
        value = a.get("value") if isinstance(a.get("value"), dict) else {}
        if owner and str(value.get("owner", "")) != str(owner):
            continue
        is_confirmed = bool(isinstance(value.get("rights"), dict))
        if confirmed is not None and is_confirmed != bool(confirmed):
            continue
        out.append(a)
    return out


def threshold_status(owner: str, home: Optional[str] = None,
                     threshold: int = AUTH_THRESHOLD) -> Dict[str, Any]:
    """按 owner+domain 统计未确权 human 经验条数，判定是否达 10 条阈值。"""
    domains: Dict[str, Dict[str, Any]] = {}
    for a in human_anchors(owner=owner, home=home, confirmed=False):
        value = a.get("value") if isinstance(a.get("value"), dict) else {}
        d = str(value.get("domain", "unknown"))
        slot = domains.setdefault(d, {"count": 0, "anchor_ids": [],
                                      "success": 0, "error": 0})
        slot["count"] += 1
        slot["anchor_ids"].append(a.get("anchor_id"))
        if str(value.get("outcome")) == "error":
            slot["error"] += 1
        else:
            slot["success"] += 1
    for d, slot in domains.items():
        slot["reached"] = slot["count"] >= int(threshold)
    return {"owner": owner, "threshold": int(threshold), "domains": domains,
            "reached_domains": sorted(d for d, s in domains.items() if s["reached"])}


def request_authorization(owner: str, domain: str, home: Optional[str] = None,
                          threshold: int = AUTH_THRESHOLD,
                          out_dir: Optional[str] = None) -> Dict[str, Any]:
    """达阈值则生成一次用户授权请求（幂等：同批经验不重复请求）。"""
    st = threshold_status(owner, home=home, threshold=threshold)
    slot = st["domains"].get(str(domain), {"count": 0, "anchor_ids": []})
    if not slot.get("reached"):
        return {"requested": False, "count": slot.get("count", 0),
                "threshold": int(threshold), "reason": "below_threshold"}

    ids = sorted(str(i) for i in slot["anchor_ids"])
    fp = _sha256("|".join(ids))[:16]
    path = os.path.join(out_dir or _data_dir(home),
                        "pending_user_authorizations.jsonl")
    if os.path.exists(path):
        try:
            for line in open(path, encoding="utf-8"):
                try:
                    prev = json.loads(line)
                except Exception:
                    continue
                prev_ids = sorted(str(e.get("anchor_id", ""))
                                  for e in prev.get("experiences", []))
                if prev_ids and _sha256("|".join(prev_ids))[:16] == fp:
                    return {"requested": False, "count": slot["count"],
                            "dedup": True, "request_id": prev.get("request_id")}
        except Exception:
            pass

    req = {"request_id": fp,
           "owner": owner,
           "domain": str(domain),
           "consent_domain": consent_domain(domain),
           "threshold": int(threshold),
           "experiences": [{"anchor_id": i} for i in ids],
           "stages_required": ["upload", "trade"],
           "trade_venue": "zeus",
           "status": "pending",
           "source": "l3_user_chain",
           "created_at": _now_iso()}
    _append_jsonl(path, req)
    return {"requested": True, "count": slot["count"], "file": path,
            "request_id": req["request_id"]}


def grant_authorization(owner: str, domain: str, home: Optional[str] = None,
                        stages: tuple = ("upload", "trade")) -> Dict[str, Any]:
    """用户显式授权：授予 upload/trade 阶段（创始人令：经用户授权确权交易）。"""
    gate = _four_stage(home)
    cd = consent_domain(domain)
    granted = [s for s in stages if gate.grant_stage(s, owner, cd)]
    return {"owner": owner, "domain": str(domain), "consent_domain": cd,
            "granted": granted, "status": gate.stage_status(owner, cd)}


def authorization_status(owner: str, domain: str,
                         home: Optional[str] = None) -> Dict[str, Any]:
    gate = _four_stage(home)
    cd = consent_domain(domain)
    return {"owner": owner, "domain": str(domain), "consent_domain": cd,
            "upload": gate.is_stage_granted("upload", owner, cd),
            "trade": gate.is_stage_granted("trade", owner, cd)}


# ── P3 · Ethan 确权打标（未授权必阻断）────────────────────────────────────────

def confirm_rights(owner: str, domain: str,
                   anchor_ids: Optional[List[str]] = None,
                   home: Optional[str] = None,
                   ethan_dir: Optional[str] = None) -> Dict[str, Any]:
    """Ethan 确权打标：upload 阶段未授权即阻断，零写入。

    ethan_dir 缺省投 ETHAN_INPUTS；测试须显式传隔离目录，避免污染生产收件箱。
    """
    from lao.effect_anchored.consent_integration import guard_upload

    gate = _four_stage(home)
    cd = consent_domain(domain)
    ok, reason = guard_upload(gate, owner, domain=cd)
    if not ok:
        return {"ok": False, "blocked": True, "reason": reason,
                "confirmed": [], "stage": "upload"}

    from lao.effect_anchored.cognitive_anchor import make_fact_anchor

    store = _anchor_store(home)
    targets = {str(a.get("anchor_id")): a
               for a in human_anchors(owner=owner, home=home, confirmed=False)
               if str((a.get("value") or {}).get("domain")) == str(domain)}
    if anchor_ids:
        targets = {k: v for k, v in targets.items() if k in set(anchor_ids)}

    confirmed: List[Dict[str, Any]] = []
    for aid, a in sorted(targets.items()):
        value = dict(a.get("value") or {})
        attestation = "sha256:" + _sha256(json.dumps(
            {"anchor_id": aid, "value": value}, ensure_ascii=False,
            sort_keys=True))
        value["rights"] = {"attestation": attestation,
                           "confirmed_by": "ethan",
                           "confirmed_at": _now_iso(),
                           "revenue_formula": REVENUE_FORMULA,
                           "trade_venue": "zeus"}
        tags = [t for t in (a.get("tags") or [])] + ["rights:confirmed"]
        store.put(make_fact_anchor(
            aid, value, source=a.get("source"),
            trust_weight=float(a.get("trust_weight", 0.8) or 0.8),
            tags=sorted(set(tags)),
            experience_type=str(a.get("experience_type", TYPE_COLLABORATIVE))))
        rec = {"anchor_id": aid, "owner": owner, "domain": str(domain),
               "attestation": attestation, "confirmed_at": value["rights"]["confirmed_at"],
               "experience_type": a.get("experience_type"),
               "source": "l3_user_chain.confirm_rights"}
        _append_jsonl(os.path.join(_data_dir(home), "ethan_notarizations.jsonl"), rec)
        _append_jsonl(os.path.join(ethan_dir or ETHAN_INPUTS,
                                   "l3_rights_confirmations.jsonl"), rec)
        confirmed.append(rec)
    return {"ok": True, "blocked": False, "confirmed": confirmed,
            "count": len(confirmed), "stage": "upload"}


# ── P4 · 上架交 Zeus（默认关闭 · 仅哈希化元数据）───────────────────────────────

def publish_to_zeus(owner: str, domain: str,
                    anchor_ids: Optional[List[str]] = None,
                    home: Optional[str] = None,
                    enabled: Optional[bool] = None,
                    inputs_dir: Optional[str] = None) -> Dict[str, Any]:
    """已确权经验上架 → Zeus inputs。默认关闭；trade 未授权即阻断。"""
    if enabled is None:
        enabled = _flag_on(TRADE_FLAG)
    if not enabled:
        return {"published": False, "count": 0, "listings": [],
                "reason": "feature_flag_off", "flag": TRADE_FLAG}

    from lao.effect_anchored.consent_integration import guard_trade

    gate = _four_stage(home)
    cd = consent_domain(domain)
    ok, reason = guard_trade(gate, owner, domain=cd)
    if not ok:
        return {"published": False, "blocked": True, "count": 0,
                "listings": [], "reason": reason, "stage": "trade"}

    targets = [a for a in human_anchors(owner=owner, home=home, confirmed=True)
               if str((a.get("value") or {}).get("domain")) == str(domain)]
    if anchor_ids:
        targets = [a for a in targets if str(a.get("anchor_id")) in set(anchor_ids)]

    path = os.path.join(inputs_dir or ZEUS_INPUTS, "l3_trade_listings.jsonl")
    listings: List[Dict[str, Any]] = []
    for a in targets:
        value = a.get("value") or {}
        rights = value.get("rights") or {}
        version = int(a.get("version", 1) or 1)
        aid = str(a.get("anchor_id"))
        # PIPL 边界：只上架哈希化元数据，经验原文一律不出本机
        listing = {
            "schema": LISTING_SCHEMA,
            "artifact_vid": "%s@%d" % (aid, version),
            "card_id": aid,
            "version": version,
            "owner_did": "did:zwf:%s" % owner,
            "domain": str(domain),
            "experience_type": str(a.get("experience_type", "")),
            "outcome": str(value.get("outcome", "")),
            "confidence": round(float(a.get("trust_weight", 0) or 0), 4),
            "content_sha256": _sha256(json.dumps(
                {"anchor_id": aid, "value": value}, ensure_ascii=False,
                sort_keys=True)),
            "attestation": rights.get("attestation", ""),
            "revenue_formula": REVENUE_FORMULA,
            "trade_venue": "zeus",
            "state": "listed",
            "listed_at": _now_iso(),
            "source": "l3_user_chain.publish_to_zeus",
        }
        _append_jsonl(path, listing)
        listings.append(listing)
    return {"published": bool(listings), "count": len(listings),
            "listings": listings, "file": path, "stage": "trade"}


# ── 链路状态（供路由端点/驾驶舱只读查询）──────────────────────────────────────

def chain_status(owner: str, home: Optional[str] = None,
                 threshold: int = AUTH_THRESHOLD) -> Dict[str, Any]:
    st = threshold_status(owner, home=home, threshold=threshold)
    confirmed = human_anchors(owner=owner, home=home, confirmed=True)
    domains: Dict[str, Any] = {}
    for d, slot in st["domains"].items():
        domains[d] = dict(slot)
        domains[d]["authorization"] = authorization_status(owner, d, home=home)
    return {"owner": owner, "threshold": int(threshold),
            "pending_by_domain": domains,
            "confirmed_count": len(confirmed),
            "trade_flag": {"name": TRADE_FLAG, "on": _flag_on(TRADE_FLAG)},
            "trade_venue": "zeus",
            "checked_at": _now_iso()}
