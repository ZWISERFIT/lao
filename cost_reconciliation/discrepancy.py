"""T6 差异报告生成。

P1-A T6 规格落地（153-T6-Design）：
    - DiscrepancyReport: 差异报告数据结构
    - 生成可读的对账报告

约束：仅标准库 · 零外部依赖
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class DiscrepancyItem:
    """单条差异记录。"""

    window: str
    provider: str
    discrepancy_type: str
    expected_cost: float = 0.0
    actual_cost: float = 0.0
    cost_diff: float = 0.0
    cost_diff_pct: float = 0.0
    expected_count: int = 0
    actual_count: int = 0
    count_diff: int = 0
    status: str = "ok"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "window": self.window,
            "provider": self.provider,
            "discrepancy_type": self.discrepancy_type,
            "expected_cost": self.expected_cost,
            "actual_cost": self.actual_cost,
            "cost_diff": self.cost_diff,
            "cost_diff_pct": self.cost_diff_pct,
            "expected_count": self.expected_count,
            "actual_count": self.actual_count,
            "count_diff": self.count_diff,
            "status": self.status,
        }


@dataclass
class DiscrepancyReport:
    """差异报告。"""

    expected_total: float
    actual_total: float
    expected_count: int
    actual_count: int
    discrepancies: List[DiscrepancyItem] = field(default_factory=list)
    status: str = "ok"
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "expected_total": self.expected_total,
            "actual_total": self.actual_total,
            "expected_count": self.expected_count,
            "actual_count": self.actual_count,
            "discrepancies": [d.to_dict() for d in self.discrepancies],
            "status": self.status,
            "notes": self.notes,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_reconciliation_result(cls, result: Dict[str, Any]) -> "DiscrepancyReport":
        """从对账结果创建报告。"""
        discrepancies = []
        for d in result.get("discrepancies", []):
            discrepancies.append(DiscrepancyItem(
                window=d.get("window", ""),
                provider=d.get("provider", ""),
                discrepancy_type=d.get("type", ""),
                expected_cost=d.get("expected_cost", 0.0),
                actual_cost=d.get("actual_cost", 0.0),
                cost_diff=d.get("cost_diff", 0.0),
                cost_diff_pct=d.get("cost_diff_pct", 0.0),
                expected_count=d.get("expected_count", 0),
                actual_count=d.get("actual_count", 0),
                count_diff=d.get("count_diff", 0),
                status=d.get("status", "ok"),
            ))
        return cls(
            expected_total=result.get("expected_total", 0.0),
            actual_total=result.get("actual_total", 0.0),
            expected_count=result.get("expected_count", 0),
            actual_count=result.get("actual_count", 0),
            discrepancies=discrepancies,
            status=result.get("status", "ok"),
            notes="【待对账】成本数字未与官方后台对账前，本报告仅供参考。",
        )

    def summary(self) -> str:
        """生成摘要文本。"""
        lines = [
            f"对账状态: {self.status}",
            f"预期总成本: ${self.expected_total:.4f} ({self.expected_count} 笔)",
            f"实际总成本: ${self.actual_total:.4f} ({self.actual_count} 笔)",
            f"差异笔数: {len(self.discrepancies)}",
        ]
        if self.discrepancies:
            lines.append("")
            lines.append("差异明细:")
            for d in self.discrepancies:
                lines.append(
                    f"  [{d.status}] {d.window} {d.provider}: "
                    f"预期${d.expected_cost:.4f} vs 实际${d.actual_cost:.4f} "
                    f"(差异{d.cost_diff_pct:.1f}%)"
                )
        lines.append("")
        lines.append(self.notes)
        return "\n".join(lines)
