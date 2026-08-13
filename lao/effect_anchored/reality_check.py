"""
RealityCheckEngine — RealityCheck Hallucination Engine (Phase2 P1-4·创始人令 v3.4)
=============================================================================
从 hallucination_gate(安全模块) 升级为用户价值模块。

每次回答生成 AnswerConfidenceEvent:
    answer_id / confidence_score / evidence_count / experience_used /
    unknown_assumption / verification_state

用户看到:
    Answer Confidence: 94%   Based on: 3 verified experiences · 2 trusted sources · 1 uncertainty

目标: 不是禁止 AI, 而是让用户知道什么时候该相信 AI。
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class AnswerConfidenceEvent:
    """一次回答的可信度评估。"""
    answer_id: str
    confidence_score: float = 0.0      # 0-100
    evidence_count: int = 0
    experience_used: List[str] = field(default_factory=list)
    unknown_assumptions: int = 0
    verification_state: str = "unverified"   # verified / sourced / partial / unverified
    ts: str = ""

    def to_trust_event(self) -> dict:
        """→ TrustEvent(可信·可审计)。"""
        return {
            "event": "AnswerConfidence",
            "subtype": "EvidenceEvent",
            "answer_id": self.answer_id,
            "confidence_score": self.confidence_score,
            "evidence_count": self.evidence_count,
            "experience_used": self.experience_used,
            "unknown_assumptions": self.unknown_assumptions,
            "verification_state": self.verification_state,
            "ts": self.ts,
        }


class RealityCheckEngine:
    """RealityCheck: 评估回答的可信度(证据+经验+不确定性)。"""

    def __init__(self, trust_memory=None):
        # 可复用 P1-3 Experience Memory(verified solutions)
        self._trust_memory = trust_memory  # 可选
        self._events: Dict[str, AnswerConfidenceEvent] = {}
        self._counter = 0

    def evaluate(self, answer_id: str,
                 evidence_count: int = 0,
                 trusted_sources: int = 0,
                 unknown_assumptions: int = 0,
                 experience_keys: Optional[List[str]] = None,
                 keyword_matches: int = 0) -> AnswerConfidenceEvent:
        """基于证据/经验/不确定性计算置信分。"""
        self._counter += 1
        exp = experience_keys or []
        ev_count = evidence_count + trusted_sources + keyword_matches

        # 基础置信(证据驱动)
        score = 0.0
        score += min(40, evidence_count * 15)        # 直接证据(最多40)
        score += min(25, trusted_sources * 12)       # 可信来源(最多25)
        score += min(20, keyword_matches * 8)        # 关键词验证(最多20)
        score += min(15, len(exp) * 8)               # 已验证经验(最多15)

        # 不确定性扣分(诚实标注不知道)
        score = max(0.0, score - unknown_assumptions * 20)

        # 归一化到 0-100
        confidence = min(100.0, score)

        # 判定状态
        if evidence_count >= 3 and unknown_assumptions == 0:
            state = "verified"
        elif trusted_sources >= 2:
            state = "sourced"
        elif evidence_count > 0 or keyword_matches > 0:
            state = "partial"
        else:
            state = "unverified"

        ev = AnswerConfidenceEvent(
            answer_id=answer_id, confidence_score=round(confidence, 1),
            evidence_count=ev_count, experience_used=exp,
            unknown_assumptions=unknown_assumptions, verification_state=state,
            ts=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        self._events[answer_id] = ev
        return ev

    def display(self, ev: AnswerConfidenceEvent) -> str:
        """用户可见的置信展示(Test3: 普通LLM无证据 vs LAO显示confidence+evidence)。"""
        return (f"Answer Confidence: {ev.confidence_score:.0f}% | "
                f"Based on: {ev.evidence_count} evidence · {len(ev.experience_used)} experience "
                f"· {ev.unknown_assumptions} uncertainty | state={ev.verification_state}")

    def events(self) -> List[AnswerConfidenceEvent]:
        return list(self._events.values())


# 核心对比: 普通 LLM(无证据) vs LAO(带证据)
def compare_un_sourced(llm_score: int = 0, lao_evidence: int = 3,
                       lao_experience: int = 2, lao_unknown: int = 1) -> dict:
    """演示: 普通LLM(无证据) vs LAO(带证据) 的置信区别(Test3核心)。"""
    engine = RealityCheckEngine()
    plain = engine.evaluate("plain", evidence_count=0, trusted_sources=0,
                            unknown_assumptions=5, experience_keys=[])
    lao = engine.evaluate("lao", evidence_count=lao_evidence,
                          trusted_sources=2, unknown_assumptions=lao_unknown,
                          experience_keys=[f"exp-{i}" for i in range(lao_experience)],
                          keyword_matches=2)
    return {"plain": engine.display(plain), "lao": engine.display(lao),
            "plain_conf": plain.confidence_score, "lao_conf": lao.confidence_score}
