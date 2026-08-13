"""Phase2 P1-5 测试: Developer SDK + Experience Loop (10分钟体验·证书)。

创始人 v3.4 P1-5: 外部开发者10分钟体验。
创建Agent→注入故障→LAO修复→查看成本下降→生成ExperienceAsset→证书。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.developer_sdk import AgentRuntime


def test_sdk_usage_enables():
    """创始人 SDK 用法: enable_trust/cost/memory 链式。"""
    agent = AgentRuntime(model="deepseek")
    agent.enable_trust().enable_cost().enable_memory()
    assert agent._trust_on and agent._cost_on and agent._memory_on


def test_failure_heal():
    """注入故障 → LAO 修复 → verified。"""
    agent = AgentRuntime(model="deepseek").enable_trust()
    result = agent.run_failure_heal("gateway")
    assert result["verified"] is True
    assert result["attestation"].startswith("sha256:")


def test_cost_saving():
    """查看成本下降(同agent original vs optimized)。"""
    agent = AgentRuntime(model="deepseek").enable_cost()
    saving = agent.record_cost_saving(in_tok=1000, out_tok=200)
    assert saving["saved"] > 0       # 原始节省额
    assert 0 < saving["ratio"] <= 1
    # 多笔后 report 才有可见(2位小数)的成本值
    for _ in range(50):
        agent.record_cost_saving(in_tok=3000, out_tok=800)
    rep = agent.cost_report()
    assert rep["optimized_cost"] < rep["original_cost"]
    assert rep["saved"] > 0


def test_generate_asset():
    """开发者贡献 → ExperienceAsset(EXP-)."""
    agent = AgentRuntime(model="deepseek")
    asset = agent.contribute_asset("Gateway Failure", "auto_heal", "gateway")
    assert asset["asset_id"].startswith("EXP-")
    assert asset["verification_pct"] == 99
    assert asset["did"].startswith("did:zwf:dev-")


def test_developer_certificate():
    """5步体验 → Developer Experience Certificate。"""
    agent = AgentRuntime(model="deepseek").enable_trust().enable_cost().enable_memory()
    demo = agent.run_developer_demo()
    cert = demo["certificate"]
    assert cert["did"].startswith("did:zwf:dev-")
    assert cert["contribution"] == "Recovery Pattern"
    assert cert["verified_pct"] == 99.0
    assert cert["asset_id"].startswith("EXP-")
    assert cert["attestation"]
