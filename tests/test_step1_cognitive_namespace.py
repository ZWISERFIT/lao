"""Phase1 Step1 测试: Cognitive 命名隔离 (C-Layer vs A-Layer)。

创始人终审 2026-08-13 P0-1: 重命名不重构·隔离语义不修改机制·保留 0.40/0.35/0.25 权重。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.cognitive_engine import (
    CognitiveSystem, CogL1RealTime, CogL2ShortTermTaste,
    CogL3LongTermJudgment, W_L1, W_L2, W_L3, DEFAULT_POLICY,
)


def test_cognitive_namespace_prefixes():
    """认知层类必须用 Cog 前缀(C-Layer), 与架构 A-Layer 隔离。"""
    assert CogL1RealTime.__name__ == "CogL1RealTime"
    assert CogL2ShortTermTaste.__name__ == "CogL2ShortTermTaste"
    assert CogL3LongTermJudgment.__name__ == "CogL3LongTermJudgment"


def test_weights_preserved():
    """创始人原始认知权重 0.40/0.35/0.25 必须保留(不可改)。"""
    assert W_L1 == 0.40 and W_L2 == 0.35 and W_L3 == 0.25
    assert DEFAULT_POLICY.l1_weight == 0.40
    assert DEFAULT_POLICY.l2_weight == 0.35
    assert DEFAULT_POLICY.l3_weight == 0.25


def test_cognitive_system_instantiates_with_cog_layers():
    """CognitiveSystem 实例化后, L1/L2/L3 必须指向 Cog 命名空间类。"""
    cs = CognitiveSystem()
    assert type(cs.L1).__name__ == "CogL1RealTime"
    assert type(cs.L2).__name__ == "CogL2ShortTermTaste"
    assert type(cs.L3).__name__ == "CogL3LongTermJudgment"


def test_mechanism_working_after_rename():
    """重命名后机制必须照常工作: L1 冲突修正/错误复利/经验复利。"""
    cs = CognitiveSystem()
    # L1 冲突修正
    fp = cs.L1.on_conflict("conf:403:test", provider="deepseek")
    assert fp.startswith("conflict:")
    # L1 错误复利(2次→升级)
    e1 = cs.L1.on_error("err:timeout:test")
    assert e1 == "err:timeout:test"
    # L1 经验复利(成功→权重+delta)
    w0 = cs._weights.get("anchor:test", 0.0)
    new_w = cs.L1.on_success("anchor:test")
    assert new_w == round(w0 + DEFAULT_POLICY.compound_delta, 4)
