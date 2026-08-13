"""Phase2 P1-4 测试: RealityCheck Hallucination Engine (Test3·置信+证据)。

创始人 v3.4 P1-4: 普通LLM无证据回答 vs LAO显示confidence+evidence。让用户知道何时该信AI。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.reality_check import (
    RealityCheckEngine, compare_un_sourced,
)


def test_lao_confidence_higher_than_plain():
    """Test3: LAO(带证据) 置信 > 普通LLM(无证据)。"""
    res = compare_un_sourced()
    assert res["lao_conf"] > res["plain_conf"]


def test_verified_with_evidence():
    """3+ 证据且无不确定性 → verified。"""
    eng = RealityCheckEngine()
    ev = eng.evaluate("a1", evidence_count=3, trusted_sources=2,
                      unknown_assumptions=0, experience_keys=["exp-a"], keyword_matches=2)
    assert ev.verification_state == "verified"
    assert ev.confidence_score >= 80


def test_unverified_without_evidence():
    """无证据+高不确定性 → unverified(诚实低置信)。"""
    eng = RealityCheckEngine()
    ev = eng.evaluate("a2", evidence_count=0, trusted_sources=0, unknown_assumptions=5)
    assert ev.verification_state == "unverified"
    assert ev.confidence_score == 0


def test_uncertainty_discounts_confidence():
    """不确定性扣置信(诚实标注不知道)。"""
    eng = RealityCheckEngine()
    high = eng.evaluate("a3", evidence_count=2, unknown_assumptions=0)
    low = eng.evaluate("a4", evidence_count=2, unknown_assumptions=5)
    assert high.confidence_score > low.confidence_score


def test_trust_event_evidence():
    """AnswerConfidenceEvent → TrustEvent(subtype=EvidenceEvent)。"""
    eng = RealityCheckEngine()
    ev = eng.evaluate("a5", evidence_count=3, experience_keys=["e1", "e2"])
    te = ev.to_trust_event()
    assert te["event"] == "AnswerConfidence"
    assert te["subtype"] == "EvidenceEvent"
    assert te["confidence_score"] >= 0
