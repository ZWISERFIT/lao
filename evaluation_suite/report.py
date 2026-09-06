"""T9 评测报告生成。

P1-A T9 规格落地（155-T9-Design）：
    - 评测报告数据结构
    - 三轮评测结果汇总
    - 改进建议生成

约束：仅标准库 · 零外部依赖
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .ab_engine import ABTestResult


@dataclass
class EvalReport:
    """评测报告。"""

    dataset_name: str
    dataset_version: str
    item_count: int
    round_count: int
    ab_result: ABTestResult
    summary: str = ""
    recommendations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dataset_name": self.dataset_name,
            "dataset_version": self.dataset_version,
            "item_count": self.item_count,
            "round_count": self.round_count,
            "ab_result": self.ab_result.to_dict(),
            "summary": self.summary,
            "recommendations": self.recommendations,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_ab_test(
        cls,
        dataset_name: str,
        dataset_version: str,
        item_count: int,
        ab_result: ABTestResult,
    ) -> "EvalReport":
        """从 A/B 测试结果生成报告。"""
        # 计算汇总统计
        if ab_result.rounds_a:
            avg_accuracy_a = sum(r.accuracy for r in ab_result.rounds_a) / len(ab_result.rounds_a)
            avg_cost_a = sum(r.avg_cost for r in ab_result.rounds_a) / len(ab_result.rounds_a)
        else:
            avg_accuracy_a = 0.0
            avg_cost_a = 0.0

        if ab_result.rounds_b:
            avg_accuracy_b = sum(r.accuracy for r in ab_result.rounds_b) / len(ab_result.rounds_b)
            avg_cost_b = sum(r.avg_cost for r in ab_result.rounds_b) / len(ab_result.rounds_b)
        else:
            avg_accuracy_b = 0.0
            avg_cost_b = 0.0

        # 生成摘要
        accuracy_improvement = avg_accuracy_b - avg_accuracy_a
        cost_saving_pct = (avg_cost_a - avg_cost_b) / avg_cost_a * 100 if avg_cost_a > 0 else 0

        summary = (
            f"A/B 评测完成 {len(ab_result.rounds_a)} 轮。"
            f"基线准确率 {avg_accuracy_a:.1%}，优化后 {avg_accuracy_b:.1%}，"
            f"提升 {accuracy_improvement:.1%}。"
            f"成本节省 {cost_saving_pct:.1f}%。"
        )

        # 生成建议
        recommendations = []
        if accuracy_improvement > 0.1:
            recommendations.append("优化策略显著提升准确率，建议上线。")
        if cost_saving_pct > 10:
            recommendations.append("优化策略显著降低成本，建议优先采用。")
        if avg_accuracy_b < 0.8:
            recommendations.append("优化策略准确率未达 80%，需进一步优化。")

        return cls(
            dataset_name=dataset_name,
            dataset_version=dataset_version,
            item_count=item_count,
            round_count=len(ab_result.rounds_a),
            ab_result=ab_result,
            summary=summary,
            recommendations=recommendations,
        )
