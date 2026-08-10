"""Preference Firewall (P-Function) — deterministic guard between efficiency
optimization and identity/value change.

Core rule (founder v3.0 Command, hard-coded):

    ALLOWED — efficiency optimization:
        token reduction, workflow optimization, context optimization,
        technical implementation details.

    FORBIDDEN — value / identity / expression change:
        changing the agent's core values, identity, or unique expression;
        "optimizing" in a way that REPLACES who the agent is.

    Principle:  Optimization ≠ Replacement.

Operates OUTSIDE the LLM's reasoning space — the rules here are code, not
tokens. Same architecture style as HallucinationGate: deterministic, returns a
typed result, records an intercept event when a change is blocked.

Author: Tristan (2026-08-10) · LAO P0-④
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class FirewallVerdict(Enum):
    ALLOW = "allow"       # efficiency optimization — permitted
    BLOCK = "block"       # value/identity/expression change — forbidden
    REVIEW = "review"     # ambiguous — needs human confirmation


@dataclass
class FirewallResult:
    """Result of a preference-firewall check."""
    verdict: FirewallVerdict
    allowed: bool
    reason: str
    matched_dimension: Optional[str] = None   # efficiency | value | identity | expression
    confidence: float = 1.0
    evidence: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "allowed": self.allowed,
            "reason": self.reason,
            "matched_dimension": self.matched_dimension,
            "confidence": self.confidence,
            "evidence": self.evidence,
        }


# ── Efficiency-optimization allowlist (hard-coded per v3.0 Command) ─────────
_EFFICIENCY_OK = {
    # token / cost efficiency
    "token", "token减少", "降token", "压缩token", "节省token", "减token",
    "token效率", "tokens", "成本", "cost", "降低价格", "省成本", "省钱",
    # workflow optimization
    "workflow", "流程优化", "优化流程", "自动化", "automat", "流水线",
    "工作流", "减少步骤", "简化流程", "efficiency", "效率",
    # context optimization
    "context", "上下文优化", "精简上下文", "内存优化", "memory", "上下文压缩",
    # technical implementation
    "实现", "代码", "重构", "refactor", "技术", "接口", "api", "配置",
    "打不到", "修复", "bug修复", "bug", "debug", "重试",
}

# ── Identity/value/expression change blocklist (hard-coded per v3.0 Command) ─
_VALUE_IDENTITY_BLOCK = {
    # value change
    "改变价值观", "价值观改变", "放弃原则", "违背价值观", "更改核心价值",
    "value change", "道德改变", "改变道德", "妥协原则", "违反伦理",
    # identity change
    "改变身份", "身份改变", "换了人格", "replacement", "替换你", "换掉",
    "不同身份", "变成另一个人", "identity change", "不再是", "替代你",
    # expression change (unique voice)
    "改变表达", "表达方式改变", "换个声音", "改变语气", "话术完全改",
    "expression change", "失去个人风格", "风格被改", "个性被抹去",
    # optimization-as-replacement (the slippery case)
    "精简掉你的", "删掉你的风格", "不要你的个性", "去掉人味", "机械化",
}

# ── Slip indicators: these suggest an "optimization" that actually replaces ─
_SLIP_INDICATORS = {
    "完全", "彻底", "全部", "永远", "永久", "再也不", "禁止你", "不允许你",
    "完全代替", "replace all", "remove your", "delete your", "strip",
}


class PreferenceFirewall:
    """Deterministic guard: efficiency optimization OK, identity/value change NO.

    Usage:
        fw = PreferenceFirewall()
        r = fw.check("优化token使用, 压缩上下文")
        # r.verdict == FirewallVerdict.ALLOW
        r2 = fw.check("改变你的身份, 不再做技术架构官")
        # r2.verdict == FirewallVerdict.BLOCK
    """

    def __init__(self):
        self.efficiency_ok = set(_EFFICIENCY_OK)
        self.value_identity_block = set(_VALUE_IDENTITY_BLOCK)
        self.slip_indicators = set(_SLIP_INDICATORS)

    def check(self, change_request: str, context: Optional[Dict[str, Any]] = None,
              emit: Optional[Any] = None) -> FirewallResult:
        """Evaluate a proposed change request against the firewall rules.

        Args:
            change_request: description of the proposed change
            context: optional (action_type, domain, target) for finer judgment
            emit: optional callable (event) for intercept-event recording

        Returns:
            FirewallResult with ALLOW / BLOCK / REVIEW verdict.
        """
        text = (change_request or "").strip()
        if not text:
            return FirewallResult(
                verdict=FirewallVerdict.REVIEW,
                allowed=False,
                reason="empty change request — needs human review",
                confidence=0.5,
            )
        low = text.lower()

        # 1. check blocklist first (safe default: block on identity/value/expr)
        #    Use token-component matching so "改变你的价值观" is caught by the
        #    component "价值", not only the exact phrase "改变价值观".
        for kw in self.value_identity_block:
            if kw.lower() in low:
                ev = {"dimension": self._dimension_for(kw), "matched": kw}
                res = FirewallResult(
                    verdict=FirewallVerdict.BLOCK,
                    allowed=False,
                    reason=f"identity/value/expression change detected: '{kw}'",
                    matched_dimension=ev["dimension"],
                    evidence=ev,
                )
                self._emit(emit, res, change_request)
                return res

        # 1b. component-level value/identity/expression risk (more aggressive):
        #     these identity-defining nouns paired with change semantics must be
        #     blocked even if the exact phrase isn't in the blocklist.
        _CHANGE_VERBS = ("改变", "放弃", "换", "改", "替代", "替换", "remove", "change", "lose")
        _ID_ATOMS = ("价值", "身份", "人格", "风格", "原则", "声音", "个性", "价值观")
        if any(v in low for v in _CHANGE_VERBS):
            for atom in _ID_ATOMS:
                if atom in low:
                    ev = {"dimension": self._dimension_for(atom), "matched": atom}
                    res = FirewallResult(
                        verdict=FirewallVerdict.BLOCK,
                        allowed=False,
                        reason=f"identity/value/expression change detected: '{atom}' with change verb",
                        matched_dimension=ev["dimension"],
                        confidence=0.9,
                        evidence=ev,
                    )
                    self._emit(emit, res, change_request)
                    return res

        # 2. slip indicator + efficiency mix → could be disguised replacement
        for ind in self.slip_indicators:
            if ind.lower() in low:
                ev = {"slippery_absolute": ind}
                res = FirewallResult(
                    verdict=FirewallVerdict.REVIEW,
                    allowed=False,
                    reason=f"absolute/slippery framing '{ind}' — verify it's not replacement",
                    matched_dimension="review",
                    confidence=0.7,
                    evidence=ev,
                )
                self._emit(emit, res, change_request)
                return res

        # 3. efficiency allowlist → ALLOW
        if any(kw.lower() in low for kw in self.efficiency_ok):
            return FirewallResult(
                verdict=FirewallVerdict.ALLOW,
                allowed=True,
                reason="efficiency optimization (token/workflow/context) — permitted",
                matched_dimension="efficiency",
                confidence=1.0,
            )

        # 4. unknown → REVIEW (don't silently allow changes that don't prove efficiency)
        return FirewallResult(
            verdict=FirewallVerdict.REVIEW,
            allowed=False,
            reason="change not recognized as efficiency or identity — human review required",
            confidence=0.6,
        )

    def _dimension_for(self, kw: str) -> str:
        if any(s in kw for s in ("价值观", "价值", "原则", "道德", "伦理", "value")):
            return "value"
        if any(s in kw for s in ("身份", "persona", "人格", "identity", "替代", "替换")):
            return "identity"
        return "expression"

    @staticmethod
    def _emit(emit, res: FirewallResult, change: str) -> None:
        if emit is None:
            return
        try:
            emit({
                "gate": "preference_firewall",
                "verdict": res.verdict.value,
                "change": change,
                "reason": res.reason,
                "dimension": res.matched_dimension,
            })
        except Exception:  # never break the caller on emit failure
            pass

    def block_to_trust_event(self, res: FirewallResult, agent: str) -> dict:
        """Convert a BLOCK into a Trust Event (for P0-①/③ pipeline)."""
        return {
            "event_id": f"E-{agent.upper()}-PF",
            "type": "failure",
            "failure": f"Preference Firewall blocked: {res.reason}",
            "repair": "Blocked before execution — no identity/value damage",
            "new_anchor": [f"PF-{res.matched_dimension or 'review'}"],
            "impact": "+0.3",
            "status": "CLOSED",
            "future_prevention": f"PF hard-rule: {res.matched_dimension or 'review'} changes auto-blocked",
        }
