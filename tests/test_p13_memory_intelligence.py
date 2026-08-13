"""Phase2 P1-3 测试: Memory Intelligence Engine (分层+压缩+复用·第二卖点)。

创始人 v3.4 P1-3: MEMORY.md 55KB → Hot 8KB + Experience 15KB + Archive 32KB。
减少 token/compaction/CPU/latency。MemoryOptimizationEvent。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.memory_intelligence import (
    MemoryIntelligenceEngine, HOT, EXPERIENCE, ARCHIVE,
)

SOURCE = {
    "current_preference": "偏好: 简体中文",          # Hot
    "active_task": "当前任务: LAO 施工",              # Hot
    "recent_decision": "决策: 方案D+",                # Hot
    "verified_gateway_fix": "已验证解法: gateway restore",   # Experience
    "successful_workflow": "成功流程: Detect->Recover",      # Experience
    "history_log_2026_07": "历史对话..." + "x" * 500,         # Archive
    "old_context_0712": "旧上下文..." + "y" * 400,            # Archive
}


def test_classify_regions():
    """启发式分层: 决策/偏好→Hot · 验证→Experience · 历史→Archive。"""
    eng = MemoryIntelligenceEngine()
    imp = eng.optimize(SOURCE)
    c = eng.region_counts()
    assert c[HOT] == 3
    assert c[EXPERIENCE] == 2
    assert c[ARCHIVE] == 2
    assert imp.compression_ratio > 0.5  # 明显压缩(Hot alone)


def test_reuse_promotes_to_hot():
    """第二次执行自动调用 Experience → 提升到 Hot(创始人 Test2)。"""
    eng = MemoryIntelligenceEngine()
    eng.optimize(SOURCE)
    assert eng.get("verified_gateway_fix").region == EXPERIENCE
    eng.promote_to_hot("verified_gateway_fix")
    assert eng.get("verified_gateway_fix").region == HOT
    assert eng.get("verified_gateway_fix").reuse_count >= 1


def test_memory_optimization_event():
    """优化 → TrustEvent(subtype=MemoryEvent·可审计)。"""
    eng = MemoryIntelligenceEngine()
    imp = eng.optimize(SOURCE)
    te = imp.to_trust_event()
    assert te["event"] == "MemoryOptimization"
    assert te["subtype"] == "MemoryEvent"
    assert te["before_tokens"] > te["after_tokens"]
    assert 0 <= te["compression_ratio"] <= 1


def test_put_get_regions():
    """默认存储 Hot + 可检索。"""
    eng = MemoryIntelligenceEngine()
    it = eng.put("立即使用的偏好", region=HOT)
    assert it.region == HOT
    assert eng.get(it.key).content == "立即使用的偏好"


def test_tokens_estimated():
    """token 估计非零。"""
    eng = MemoryIntelligenceEngine()
    it = eng.put("一段需要记忆的重要上下文内容用于测试", key="k1")
    assert it.tokens > 0
