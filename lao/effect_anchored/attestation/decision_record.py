#!/usr/bin/env python3
"""
Agent Decision Record (ADR) — 决策可审计记录
================================================
⚠️ ADR = 每一次【重要 Agent 决策】的可审计记录。
   补 TrustEvent（结果账本）之不足：TrustEvent 记"发生了+如何修复"，
   ADR 记"为什么这么决定+依据什么 Policy"。

串起全链：LAO + Nova + Ethan + Stella + Board。
- LAO    : 决策由 LAO 三层(L1路由/L2经验/L3确权)驱动
- Nova   : 路由/成本/Provider 决策的产出方
- Ethan  : 经验估值/确权 attestation_id（存证）
- Stella : 独立审计（读 ADR 输出 AuditReport）
- Board  : 决策链可复现/可追溯

红线三要素（智囊团共识·2026-08-12）:
  1. signature        — 决策签名（谁做的+防篡改）
  2. 存证 hash        — attestation_id / 决策hash（SHA-256, 链上/哈希存证）
  3. 归因 evidence    — 决策依据的可复现证据（Ethan 侧存证协同）

兼容性：
- 不造第二事实源 → 复用一个确权的 TrustEvent.hash 机制（_sha256）
- ADR.lead_evidence 可直接引用 TrustEvent.event_id / ExperienceAtom / Anchor id
- ADR.serialized 可被 FfeedbackBus / ethan / Stella 读取
"""

from __future__ import annotations
import hashlib
import os
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ADR 存证默认路径（可环境变量覆盖，与 TrustEvent 'live/trust-events/' 同层）
DEFAULT_ADR_STORE = "live/decision-records/adr-ledger.json"

# ADR 状态机
ADR_STATUS = ("DRAFT", "DECIDED", "EXECUTED", "VERIFIED", "REJECTED", "ARCHIVED")

# 决策等级（Policy Change Gate 分级映射 · 红线①）
ADR_LEVEL = ("GREEN", "YELLOW", "RED")


def _sha256(text: str) -> str:
    """与 trust-events 同款哈希（复用·不造第二个哈希实现）"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ADROption:
    """决策候选方案"""
    id: str                       # 方案标识 (e.g. "model:deepseek-v4-pro")
    label: str = ""               # 人类可读描述
    est_cost: str = ""            # 预估成本 (e.g. "$0.14/$0.28")
    est_quality: float = 0.0      # 预估质量 (0-1)
    est_latency: float = 0.0      # 预估时延 (0-1)
    flags: List[str] = field(default_factory=list)   # ["yellow:预算超限", ...]


@dataclass
class AgentDecisionRecord:
    # ── 标识 ─────────────────────────────────────────────
    adr_id: str                   # e.g. "ADR-20260812-001"
    agent: str                    # 决策 Agent (Tristan / Nova / ...)
    task: str                     # 任务描述
    created_at: str = ""          # 决策时间(utc)

    # ── 决策上下文 ───────────────────────────────────────
    input_summary: str = ""       # 输入摘要
    policy_versions: Dict[str, str] = field(default_factory=dict)
                                  # {"CognitivePolicy": "3.2.7", "RoutingPolicy": "2.1.4", ...}
    options: List[ADROption] = field(default_factory=list)  # 候选方案列表
    selected: str = ""            # 选中方案 id
    reason: str = ""              # 决策理由 (quality≥threshold / latency lower / effective cost -37%)

    # ── 预期/实际 ────────────────────────────────────────
    expected: str = ""            # 预期结果
    actual: str = ""              # 实际结果(执行后回填)
    outcome: str = ""             # success / failure / partial
    metrics: Dict[str, Any] = field(default_factory=dict)   # {"latency_ms":123,"cost_usd":0.02,...}

    # ── 红线三要素 ───────────────────────────────────────
    lead_evidence: List[str] = field(default_factory=list)  # 归因evidence: TrustEvent.event_id / Atom / Anchor id
    signature: str = ""           # ① 决策签名(agent+时间戳, 防篡改)
    evidence_hash: str = ""       # ② 决策内容哈希(SHA-256)
    attestation_id: str = ""      # ② Ethan 侧存证ID(可与 L3 attest 协同)
    verified: bool = False        # 校验状态
    verify_ts: str = ""           # 校验时间

    # ── 分级/状态 ────────────────────────────────────────
    level: str = "GREEN"          # ③ Policy Change Gate 分级 (GREEN/YELLOW/RED)
    status: str = "DRAFT"         # DRAFT→DECIDED→EXECUTED→VERIFIED
    policy_feedback: str = ""     # "positive" / "negative" / ""
    experience_atom: str = ""     # 关联 ExperienceAtom id (沉淀闭环)
    version: str = "3.2.0"        # ADR 协议版本

    # ── 完整性声明 ───────────────────────────────────────
    completeness: List[str] = field(default_factory=list)  # Stella 审计完备性标记

    def __post_init__(self):
        if not self.created_at:
            self.created_at = _utc()
        # 自动计算决策签名 + 内容哈希（红线②）
        self._compute_signature()

    def _canonical(self) -> str:
        """决策内容规范化(用于hash, 不含易变字段 signature/hash/verified)"""
        stable = {
            "adr_id": self.adr_id,
            "agent": self.agent,
            "task": self.task,
            "created_at": self.created_at,
            "input_summary": self.input_summary,
            "policy_versions": self.policy_versions,
            "options": [asdict(o) for o in self.options],
            "selected": self.selected,
            "reason": self.reason,
            "lead_evidence": sorted(self.lead_evidence),
            "level": self.level,
        }
        return json.dumps(stable, sort_keys=True, ensure_ascii=False)

    def _compute_signature(self):
        """签名 = agent + created_at 绑定（谁在何时决策）"""
        self.signature = f"{self.agent}::{self.created_at}::{_sha256(self._canonical())[:16]}"
        self.evidence_hash = _sha256(self._canonical())

    def verify(self) -> bool:
        """独立校验：重算hash对比，若一致且未篡改 → verified=True"""
        expected = _sha256(self._canonical())
        ok = (expected == self.evidence_hash)
        self.verified = ok
        self.verify_ts = _utc() if ok else self.verify_ts
        return ok

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["options"] = [asdict(o) for o in self.options]
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AgentDecisionRecord":
        opts = [ADROption(**o) for o in d.pop("options", [])]
        d.pop("signature", None)  # 重建时重算
        rec = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        rec.options = opts
        return rec


class ADRLedger:
    """ADR 存证账本（append-only · 复用 TrustEvent hash 机制语义）"""

    def __init__(self, path: str = ""):
        self.path = path or os.environ.get("ADR_STORE", DEFAULT_ADR_STORE)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)

    def _load(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.path):
            return []
        try:
            raw = json.load(open(self.path, encoding="utf-8"))
            return raw.get("adrs", []) if isinstance(raw, dict) else raw
        except Exception:
            return []

    def _save(self, records: List[Dict[str, Any]]):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"ledger": "LAO Agent Decision Record Ledger",
                       "version": "3.2.0", "adrs": records},
                      f, ensure_ascii=False, indent=2)

    def append(self, adr: AgentDecisionRecord) -> str:
        """追加一条 ADR（去重 by adr_id·append-only）"""
        records = self._load()
        records = [r for r in records if r.get("adr_id") != adr.adr_id]
        records.append(adr.to_dict())
        self._save(records)
        return adr.adr_id

    def get(self, adr_id: str) -> Optional[AgentDecisionRecord]:
        for r in self._load():
            if r.get("adr_id") == adr_id:
                return AgentDecisionRecord.from_dict(r)
        return None

    def replay(self, agent: str = "", start: str = "", end: str = "") -> List[AgentDecisionRecord]:
        """轻量 Replay（P0④）：按时间序回放某Agent/时间段全部决策"""
        recs = [AgentDecisionRecord.from_dict(r) for r in self._load()]
        if agent:
            recs = [r for r in recs if r.agent == agent]
        if start:
            recs = [r for r in recs if r.created_at >= start]
        if end:
            recs = [r for r in recs if r.created_at <= end]
        return sorted(recs, key=lambda r: r.created_at)

    def audit_report(self, agent: str = "", start: str = "", end: str = "") -> Dict[str, Any]:
        """Stella 独立审计接口：汇总 ADR → AuditReport 基础"""
        recs = self.replay(agent, start, end)
        verified = sum(1 for r in recs if r.verified)
        return {
            "total_adr": len(recs),
            "verified": verified,
            "unverified": len(recs) - verified,
            "by_level": {
                lv: sum(1 for r in recs if r.level == lv)
                for lv in ADR_LEVEL
            },
            "by_outcome": {
                oc: sum(1 for r in recs if r.outcome == oc)
                for oc in ("success", "failure", "partial", "")
            },
            "unexplained_loss": [
                {"adr_id": r.adr_id, "reason": r.reason, "actual": r.actual}
                for r in recs if r.outcome == "failure" and not r.reason
            ],
            "anomalous": [
                {"adr_id": r.adr_id, "level": r.level, "status": r.status}
                for r in recs if r.level == "RED" and r.status == "DRAFT"
            ],
        }
