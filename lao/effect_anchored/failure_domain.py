"""
FailureDomainDetector — Correlated Failure Detection + Failure Domain (P0-8/P0-9)
=============================================================================
创始人终审 Phase1 Step4 + ChatGPT P0-8/P0-9:

真实事故不是某个 Agent 单独失败, 而是 9 Agent **同时降级**。
LAO 必须先判断:
    Single Agent Failure  ←→  System Failure Domain

FailureDomain(统一故障域·TrustEvent subtype·非新模块):
    ui | gateway | network | session | agent | model | provider | tool | context | auth

策略: Correlated Failure Detection
    多 Agent 同时异常 → 优先找共同依赖(gateway/network/provider), 不逐个乱 restart。

信任原则:
- 不逐个 restart(会放大事故) → 先诊断共同故障域
- FailureDomain 是 TrustEvent 的 domain 字段(Step2已加), 不是再造事实源
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# 统一故障域(ChatGPT P0-9)
FAILURE_DOMAINS = [
    "ui", "gateway", "network", "session", "agent",
    "model", "provider", "tool", "context", "auth",
]

# 共同依赖映射: 各故障域 → 影响的所有 agent 子域(诊断用)
COMMON_DEPENDENCY = {
    "gateway":  ["gateway_heartbeat", "websocket", "session", "agent"],
    "network":  ["connectivity", "provider_auth", "timeout"],
    "provider": ["model_timeout", "model_auth", "provider_unavailable"],
    "context":  ["context_oversize", "compaction_abnormal", "bootstrap_reinject"],
    "auth":     ["api_key_invalid", "device_auth", "ws_auth"],
    "ui":       ["control_plane", "browser_login", "tls"],
}


@dataclass
class AgentSignal:
    """一个 Agent 的故障信号(RuntimeEvent 观测)。"""
    agent: str
    symptom: str              # 症状描述
    domains: List[str] = field(default_factory=list)   # 疑似域(可多)
    ts: str = ""
    healthy: bool = False


@dataclass
class FailureDomainDecision:
    """Correlated Failure 诊断结果。"""
    correlated: bool = False          # 是否系统性故障(多agent)
    affected_agents: List[str] = field(default_factory=list)
    common_dependency: str = ""       # 共同故障域(gateway/network/provider/...)
    candidates: List[str] = field(default_factory=list)   # 候选共同依赖
    single_agent_only: bool = False
    recommendation: str = ""          # 建议(查共同依赖 vs 单点restart)
    ts: str = ""


class FailureDomainDetector:
    """Correlated Failure Detection: 多 Agent 异常 → 找共同故障域。"""

    def diagnose(self, signals: List[AgentSignal]) -> FailureDomainDecision:
        """输入多个 Agent 的故障信号 → 判断是系统性还是单点。"""
        d = FailureDomainDecision()
        d.ts = __import__("time").strftime("%Y-%m-%dT%H:%M:%S%z")
        unhealthy = [s for s in signals if not s.healthy]
        d.affected_agents = [s.agent for s in unhealthy]
        d.single_agent_only = len(unhealthy) <= 1

        if d.single_agent_only:
            # 单 Agent 故障 → 按该 agent 域单独处理
            d.recommendation = "single_agent_failure: 按症状域单独恢复(不牵动系统)"
            return d

        # 系统性故障(Correlated): 统计共同域
        domain_count: Dict[str, int] = {}
        all_domains: Dict[str, List[str]] = {}
        for s in unhealthy:
            for dom in s.domains:
                domain_count[dom] = domain_count.get(dom, 0) + 1
                all_domains.setdefault(dom, []).append(s.agent)

        d.correlated = True
        # 找到覆盖最多 agent 的域(共同依赖)
        if domain_count:
            d.common_dependency = max(domain_count, key=lambda k: domain_count[k])
            d.candidates = sorted(domain_count, key=lambda k: -domain_count[k])

        # 推荐: 优先查共同依赖, 不逐个 restart
        if d.common_dependency:
            deps = COMMON_DEPENDENCY.get(d.common_dependency, [])
            d.recommendation = (
                f"systemic_failure: common_dependency={d.common_dependency} "
                f"(覆盖 {domain_count.get(d.common_dependency,0)}/{len(unhealthy)} agents) "
                f"→ 优先诊断 {d.common_dependency} 共享依赖, 不逐个 restart"
            )
        return d

    def to_trust_event(self, d: FailureDomainDecision) -> dict:
        """→ TrustEvent 负载(domain 字段·可审计)。"""
        return {
            "event": "FailureDomainDetected",
            "subtype": "RuntimeEvent",
            "domain": d.common_dependency or "agent",
            "correlated": d.correlated,
            "affected_agents": d.affected_agents,
            "common_dependency": d.common_dependency,
            "candidates": d.candidates,
            "single_agent_only": d.single_agent_only,
            "recommendation": d.recommendation,
            "ts": d.ts,
        }
