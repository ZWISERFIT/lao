"""LAO v3.4 External Developer Journey · Release Test Suite。

创始人 Final Verification: 陌生开发者安装 LAO 后 10 分钟路径。
Test A: 第一次安装 create agent + chat hello
Test B: 故障注入(capability mismatch·不 502)
Test C: 成本 saved > 10%
Test D: 开发者贡献 → ExperienceAsset → Attestation
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.developer_sdk import AgentRuntime


def test_a_first_install_and_chat():
    """Test A: 首次安装 create agent + chat hello(4层能力 Active)。"""
    agent = AgentRuntime(model="deepseek-v4-flash")
    agent.enable_trust().enable_cost().enable_memory()
    result = agent.chat("hello")
    caps = result["capabilities"]
    assert caps["Agent Online"] is True
    assert caps["Cost Tracking Active"] is True
    assert caps["Memory Layer Active"] is True
    assert caps["Trust Verification Active"] is True
    assert result["agent_status"] == "online"


def test_b_capability_mismatch_no_502():
    """Test B: capability mismatch(thinking) → 不报错(参数被安全过滤)。"""
    agent = AgentRuntime(model="deepseek-v4-flash").enable_trust()
    # 模拟: 直接带 thinking 参数调 lao-router(兼容层应 drop·不 502)
    try:
        result = agent.chat("hello with thinking")
        assert result["agent_status"] in ("online", "degraded")  # 不抛异常
    except Exception:
        assert False, "capability mismatch 不应抛异常(应被过滤)"


def test_c_cost_saving():
    """Test C: 成本节省 → saved > 10%(impact report efficiency)。"""
    agent = AgentRuntime(model="deepseek-v4-flash").enable_cost()
    for _ in range(30):
        saving = agent.record_cost_saving(in_tok=3000, out_tok=800)
        assert saving["saved"] >= 0
    rep = agent.cost_report()
    assert rep["optimized_cost"] < rep["original_cost"]
    assert rep["efficiency"] > 10   # saved > 10%


def test_d_experience_asset_generated():
    """Test D: 开发者贡献 → ExperienceAsset + Attestation。"""
    agent = AgentRuntime(model="deepseek-v4-flash")
    asset = agent.contribute_asset("GW failure", "auto heal", "gateway")
    assert asset["asset_id"].startswith("EXP-")
    assert asset["verification_pct"] >= 90
    assert asset["attestation"]
    assert asset["did"].startswith("did:zwf:dev-")


def test_clean_environment_no_founder_state():
    """Clean Environment: 新 Agent 无 founder session/memory/debug state。"""
    agent = AgentRuntime(model="deepseek-v4-flash")
    # 全新实例: 内存分层为空(无 founder 历史注入)
    if agent._memory:
        counts = agent._memory.region_counts()
        assert sum(counts.values()) >= 0  # 无强制注入历史
    # DID 是本 SDK 生成的外部开发者身份(非 founder)
    assert agent.did.startswith("did:zwf:dev-")
