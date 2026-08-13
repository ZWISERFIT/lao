"""Phase2 P0-4 测试: Recovery Experience Replay(二次故障→调历史经验·Test4)。

创始人 v3.4 P0-4: 从"能恢复"→"能学习"。Failure→Search→Recommend→Verify→Update。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.recovery_replay import RecoveryMemory


def test_record_and_search_same_domain():
    """记录恢复经验 → 同 domain 可检索。"""
    mem = RecoveryMemory()
    mem.record(domain="gateway", symptom="port blocked", solution="restart_gateway", outcome=True)
    hits = mem.search("gateway", "port blocked")
    assert len(hits) >= 1
    assert hits[0].solution == "restart_gateway"


def test_recommend_second_similar_failure():
    """第二次同类故障 → 推荐历史解法 + 成功概率(创始人 Test4)。"""
    mem = RecoveryMemory()
    for _ in range(2):
        mem.record(domain="gateway", symptom="port blocked", solution="restart_gateway",
                   outcome=True, verification_pct=98)
    r = mem.recommend(domain="gateway", symptom="port blocked")
    assert r.matched is True
    assert r.solution == "restart_gateway"
    assert r.success_probability >= 0.9
    assert r.confidence == "high"


def test_unknown_domain_no_false_recommendation():
    """全新故障域(无经验) → matched=False, 不误推。"""
    mem = RecoveryMemory()
    mem.record(domain="gateway", symptom="port blocked", solution="restart_gateway", outcome=True)
    r = mem.recommend(domain="unknown_new_domain", symptom="no experience")
    assert r.matched is False
    assert r.solution == ""


def test_success_probability_from_history():
    """成功概率 = 历史 success/(success+fail)。"""
    mem = RecoveryMemory()
    mem.record(domain="provider", symptom="timeout", solution="retry", outcome=True)
    mem.record(domain="provider", symptom="timeout", solution="retry", outcome=True)
    mem.record(domain="provider", symptom="timeout", solution="retry", outcome=False)
    rst = mem.search("provider", "timeout")[0]
    assert rst.success_rate == round(2 / 3, 3)


def test_trust_event_emitted():
    """推荐 → TrustEvent(RecoveryRecommended·可审计)。"""
    mem = RecoveryMemory()
    mem.record(domain="gateway", symptom="port", solution="restart", outcome=True)
    r = mem.recommend(domain="gateway", symptom="port")
    te = mem.to_trust_event(r)
    assert te["event"] == "RecoveryRecommended"
    assert te["subtype"] == "RecoveryEvent"
    assert te["matched"] is True
