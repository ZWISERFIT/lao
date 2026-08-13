"""
ModelIntelligenceMatrix — Provider Intelligence Matrix (Phase2 P1-2·创始人令 v3.4)
=============================================================================
从"价格路由"升级为"智能路由"(Intelligence Routing)。

lao-router 已真实运行(价格路由)。P1-2 建立 Provider/MODEL 智能矩阵:

    Provider | Model | Cost | Latency | Quality | FailureRate |
    ContextCapacity | TaskFit

Router 决策链:
    Task → Capability Match → Cost Constraint → Quality Gate → Model Decision

示例:
    DeepSeek Flash:  coding 92 / reasoning 70 / cost 98
    DeepSeek Pro:    coding 95 / reasoning 98 / cost 55
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ModelCapability:
    """单一模型的多维能力画像。"""
    provider: str
    model: str
    cost_score: float = 50.0         # 0-100 (越高越便宜)
    latency_ms: float = 0.0
    quality: float = 80.0            # 0-100
    failure_rate: float = 0.0        # 0-1
    context_capacity: int = 0        # tokens
    task_fit: Dict[str, float] = field(default_factory=dict)  # task → 适配度0-100
    base_cost_usd_per_1k: float = 0.0

    def task_score(self, task: str) -> float:
        """该模型对某任务的能力分(0-100)。"""
        return self.task_fit.get(task, self.quality * 0.7 + self.cost_score * 0.3)


class ProviderIntelligenceMatrix:
    """Provider/MODEL 智能矩阵 + 智能路由决策。"""

    def __init__(self):
        self._models: Dict[str, ModelCapability] = {}
        self._counter = 0

    def register(self, m: ModelCapability) -> None:
        self._models[m.model] = m

    def get(self, model: str) -> Optional[ModelCapability]:
        return self._models.get(model)

    # -- 智能路由决策 --
    def route(self, task: str, cost_budget: float = 1.0,
              min_quality: float = 0.0, latency_pref: str = "balanced") -> dict:
        """决策链: Task → Capability Match → Cost Constraint → Quality Gate → Model Decision。"""
        # 1. Capability Match: 按任务适配度排序
        candidates = sorted(self._models.values(),
                            key=lambda m: -m.task_score(task))
        # 2. Cost Constraint: 过滤超预算
        within_budget = [m for m in candidates
                         if m.base_cost_usd_per_1k <= cost_budget]
        pool = within_budget if within_budget else candidates
        # 3. Quality Gate: 过滤质量过低
        pool_q = [m for m in pool if m.quality >= min_quality]
        pool = pool_q if pool_q else pool
        # 4. Latency 偏好调整
        if latency_pref == "low":
            pool = sorted(pool, key=lambda m: m.latency_ms)
        # 5. Model Decision
        chosen = pool[0] if pool else candidates[0]
        return {
            "task": task, "chosen_model": chosen.model,
            "provider": chosen.provider, "task_score": round(chosen.task_score(task), 1),
            "quality": chosen.quality, "cost_score": chosen.cost_score,
            "latency_ms": chosen.latency_ms,
            "cost_within_budget": bool(within_budget),
            "quality_gate_passed": bool(pool_q),
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }

    def to_trust_event(self, decision: dict) -> dict:
        """→ TrustEvent(经济/路由·可审计)。"""
        return {
            "event": "ModelDecision",
            "subtype": "EconomicEvent",
            "task": decision["task"], "chosen_model": decision["chosen_model"],
            "provider": decision["provider"], "task_score": decision["task_score"],
            "quality": decision["quality"], "cost_score": decision["cost_score"],
            "cost_within_budget": decision["cost_within_budget"],
            "quality_gate_passed": decision["quality_gate_passed"],
            "ts": decision["ts"],
        }

    def all(self) -> List[ModelCapability]:
        return list(self._models.values())


# 便捷工厂: 默认矩阵(Test 用)
def make_default_matrix():
    m = ProviderIntelligenceMatrix()
    m.register(ModelCapability(
        provider="deepseek", model="deepseek-v4-flash", cost_score=98,
        latency_ms=800, quality=88, failure_rate=0.02, context_capacity=64000,
        task_fit={"coding": 92, "reasoning": 70, "light": 98, "cn_explain": 95},
        base_cost_usd_per_1k=0.001))
    m.register(ModelCapability(
        provider="deepseek", model="deepseek-v4-pro", cost_score=55,
        latency_ms=1800, quality=96, failure_rate=0.005, context_capacity=128000,
        task_fit={"coding": 95, "reasoning": 98, "heavy": 98, "code": 95},
        base_cost_usd_per_1k=0.003))
    return m
