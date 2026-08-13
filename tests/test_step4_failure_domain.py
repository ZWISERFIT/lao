"""Phase1 Step4 测试: Correlated Failure Detection + Failure Domain (P0-8/P0-9)。

创始人终审 2026-08-13 Phase1 Step4 + ChatGPT P0-8/9:
多 Agent 同时异常 → 优先找共同依赖(gateway/network/provider), 不逐个 restart。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.failure_domain import (
    FailureDomainDetector, AgentSignal, FAILURE_DOMAINS,
)

AGENTS = ["stella", "zeus", "shuyu", "tristan", "nova", "momo", "ethan", "baron", "luna"]


def test_single_agent_failure():
    """单 Agent 故障 → single_agent_only, 不牵动系统。"""
    det = FailureDomainDetector()
    d = det.diagnose([AgentSignal("stella", "cardano timeout", ["model"], healthy=False)])
    assert d.single_agent_only is True
    assert d.correlated is False
    assert "single_agent_failure" in d.recommendation


def test_correlated_failure_finds_common_dependency():
    """9 Agent 同时异常(共同依赖 gateway) → correlated + common_dependency=gateway。"""
    det = FailureDomainDetector()
    d = det.diagnose([AgentSignal(a, "degraded", ["gateway", "agent"], healthy=False) for a in AGENTS])
    assert d.correlated is True
    assert d.common_dependency == "gateway"
    assert len(d.affected_agents) == 9
    assert "common_dependency=gateway" in d.recommendation


def test_respect_domain_candidates():
    """多数 agent 指向 provider 域 → 共同依赖应为 provider。"""
    det = FailureDomainDetector()
    d = det.diagnose([AgentSignal(a, "model 401", ["provider", "model"], healthy=False) for a in AGENTS])
    assert d.common_dependency == "provider"
    assert d.candidates[0] == "provider"


def test_trust_event_emitted():
    """诊断结果必须产出 TrustEvent(domain 字段·可审计)。"""
    det = FailureDomainDetector()
    d = det.diagnose([AgentSignal(a, "degraded", ["network"], healthy=False) for a in AGENTS])
    te = det.to_trust_event(d)
    assert te["event"] == "FailureDomainDetected"
    assert te["subtype"] == "RuntimeEvent"
    assert te["domain"] == "network"
    assert te["correlated"] is True
    assert len(te["affected_agents"]) == 9


def test_failure_domains_defined():
    """统一故障域清单已定义(TrustEvent domain 合法值)。"""
    assert "gateway" in FAILURE_DOMAINS
    assert "provider" in FAILURE_DOMAINS
    assert "context" in FAILURE_DOMAINS
    assert "auth" in FAILURE_DOMAINS
