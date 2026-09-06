"""T9 A/B 评测引擎。

P1-A T9 规格落地（155-T9-Design）：
    - A/B 评测引擎
    - A 组：基线策略（随机路由）
    - B 组：优化策略（LAO 路由）
    - 三轮评测验证稳定性

约束：仅标准库 · 零外部依赖
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .dataset import EvalDataset, EvalItem


@dataclass
class EvalResult:
    """单个评测结果。"""

    item_id: str
    actual_provider: str
    expected_provider: str
    cost_usd: float
    latency_ms: float
    quality_score: float  # 0-1
    is_correct: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_id": self.item_id,
            "actual_provider": self.actual_provider,
            "expected_provider": self.expected_provider,
            "cost_usd": self.cost_usd,
            "latency_ms": self.latency_ms,
            "quality_score": self.quality_score,
            "is_correct": self.is_correct,
        }


@dataclass
class RoundResult:
    """单轮评测结果。"""

    round_id: int
    results: List[EvalResult] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        """路由准确率。"""
        if not self.results:
            return 0.0
        correct = sum(1 for r in self.results if r.is_correct)
        return correct / len(self.results)

    @property
    def avg_cost(self) -> float:
        """平均成本。"""
        if not self.results:
            return 0.0
        return sum(r.cost_usd for r in self.results) / len(self.results)

    @property
    def avg_latency(self) -> float:
        """平均延迟。"""
        if not self.results:
            return 0.0
        return sum(r.latency_ms for r in self.results) / len(self.results)

    @property
    def avg_quality(self) -> float:
        """平均质量评分。"""
        if not self.results:
            return 0.0
        return sum(r.quality_score for r in self.results) / len(self.results)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "round_id": self.round_id,
            "item_count": len(self.results),
            "accuracy": round(self.accuracy, 4),
            "avg_cost": round(self.avg_cost, 6),
            "avg_latency": round(self.avg_latency, 2),
            "avg_quality": round(self.avg_quality, 4),
        }


@dataclass
class ABTestResult:
    """A/B 测试结果。"""

    strategy_a: str
    strategy_b: str
    rounds: List[RoundResult] = field(default_factory=list)
    rounds_a: List[RoundResult] = field(default_factory=list)
    rounds_b: List[RoundResult] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_a": self.strategy_a,
            "strategy_b": self.strategy_b,
            "round_count": len(self.rounds),
            "rounds_a": [r.to_dict() for r in self.rounds_a],
            "rounds_b": [r.to_dict() for r in self.rounds_b],
        }


class ABEngine:
    """A/B 评测引擎。

    合成验证说明：
        本类使用合成数据模拟路由结果，不涉及真实 Provider 调用。
    """

    def __init__(self, seed: int = 42) -> None:
        self._rng = random.Random(seed)

    def simulate_baseline(self, item: EvalItem) -> EvalResult:
        """模拟基线策略（随机路由）。"""
        providers = ["deepseek", "qwen", "token-plan", "novarouteai"]
        actual = self._rng.choice(providers)
        return EvalResult(
            item_id=item.item_id,
            actual_provider=actual,
            expected_provider=item.expected_provider,
            cost_usd=self._rng.uniform(0.001, 0.05),
            latency_ms=self._rng.uniform(100, 2000),
            quality_score=self._rng.uniform(0.5, 0.9),
            is_correct=(actual == item.expected_provider),
        )

    def simulate_optimized(self, item: EvalItem) -> EvalResult:
        """模拟优化策略（LAO 路由，有偏置）。"""
        # 优化策略有 80% 概率路由正确
        if self._rng.random() < 0.8:
            actual = item.expected_provider
        else:
            providers = ["deepseek", "qwen", "token-plan", "novarouteai"]
            actual = self._rng.choice(providers)

        return EvalResult(
            item_id=item.item_id,
            actual_provider=actual,
            expected_provider=item.expected_provider,
            cost_usd=self._rng.uniform(0.001, 0.03),  # 优化后成本更低
            latency_ms=self._rng.uniform(80, 1500),    # 优化后延迟更低
            quality_score=self._rng.uniform(0.6, 0.95), # 优化后质量更高
            is_correct=(actual == item.expected_provider),
        )

    def run_round(
        self,
        dataset: EvalDataset,
        strategy: str = "baseline",
        round_id: int = 1,
    ) -> RoundResult:
        """运行单轮评测。"""
        results = []
        for item in dataset.items:
            if strategy == "baseline":
                result = self.simulate_baseline(item)
            else:
                result = self.simulate_optimized(item)
            results.append(result)
        return RoundResult(round_id=round_id, results=results)

    def run_ab_test(
        self,
        dataset: EvalDataset,
        rounds: int = 3,
    ) -> ABTestResult:
        """运行 A/B 测试（多轮）。"""
        result = ABTestResult(strategy_a="baseline", strategy_b="optimized")

        for r in range(1, rounds + 1):
            # A 组：基线
            round_a = self.run_round(dataset, strategy="baseline", round_id=r)
            result.rounds_a.append(round_a)

            # B 组：优化
            round_b = self.run_round(dataset, strategy="optimized", round_id=r)
            result.rounds_b.append(round_b)

            result.rounds.append(round_a)
            result.rounds.append(round_b)

        return result
