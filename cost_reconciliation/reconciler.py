"""T6 对账引擎 — 预期 vs 实际成本比对。

P1-A T6 规格落地（153-T6-Design）：
    - Reconciler: 对账引擎
    - 导入 LAO 路由日志（预期成本）
    - 导入 Provider 账单（实际成本）
    - 按时间窗口聚合，计算差异

约束：仅标准库 · 零外部依赖
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timezone

UTC = timezone.utc


@dataclass
class CostRecord:
    """标准化成本记录（与 CostEvent 对齐）。"""

    ts: str
    provider: str
    model: str
    cost_usd: float
    tokens_in: int = 0
    tokens_out: int = 0
    task_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts": self.ts,
            "provider": self.provider,
            "model": self.model,
            "cost_usd": self.cost_usd,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "task_id": self.task_id,
        }


@dataclass
class AggregatedCost:
    """聚合成本（按时间窗口 + Provider）。"""

    window_start: str
    window_end: str
    provider: str
    total_cost_usd: float
    record_count: int
    total_tokens_in: int = 0
    total_tokens_out: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "window_start": self.window_start,
            "window_end": self.window_end,
            "provider": self.provider,
            "total_cost_usd": self.total_cost_usd,
            "record_count": self.record_count,
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
        }


@dataclass
class ReconciliationConfig:
    """对账配置。"""

    window_hours: int = 24          # 聚合窗口（小时）
    alert_threshold_pct: float = 1.0   # 告警阈值（%）
    block_threshold_pct: float = 5.0   # 阻断阈值（%）

    def validate(self) -> List[str]:
        errors = []
        if self.window_hours <= 0:
            errors.append("window_hours 必须为正整数")
        if self.alert_threshold_pct <= 0:
            errors.append("alert_threshold_pct 必须为正数")
        if self.block_threshold_pct <= self.alert_threshold_pct:
            errors.append("block_threshold_pct 必须 > alert_threshold_pct")
        return errors


class Reconciler:
    """对账引擎。

    合成验证说明：
        本类仅处理传入的成本记录，不涉及任何真实数据。
    """

    def __init__(self, config: Optional[ReconciliationConfig] = None) -> None:
        self._config = config or ReconciliationConfig()

    @property
    def config(self) -> ReconciliationConfig:
        return self._config

    def aggregate(
        self,
        records: List[CostRecord],
        window_hours: Optional[int] = None,
    ) -> List[AggregatedCost]:
        """按时间窗口 + Provider 聚合成本。

        Args:
            records: 成本记录列表。
            window_hours: 聚合窗口（小时），默认取配置值。

        Returns:
            聚合成本列表。
        """
        if not records:
            return []

        hours = window_hours or self._config.window_hours
        # 按 Provider 分组
        by_provider: Dict[str, List[CostRecord]] = {}
        for r in records:
            by_provider.setdefault(r.provider, []).append(r)

        result = []
        for provider, recs in by_provider.items():
            # 按时间排序
            recs.sort(key=lambda x: x.ts)
            if not recs:
                continue

            # 简单按窗口聚合（按天分组）
            windows: Dict[str, List[CostRecord]] = {}
            for r in recs:
                # 取日期部分作为窗口键
                day_key = r.ts[:10] if len(r.ts) >= 10 else r.ts
                windows.setdefault(day_key, []).append(r)

            for day_key, day_recs in windows.items():
                total_cost = sum(r.cost_usd for r in day_recs)
                total_in = sum(r.tokens_in for r in day_recs)
                total_out = sum(r.tokens_out for r in day_recs)
                result.append(AggregatedCost(
                    window_start=day_key,
                    window_end=day_key,
                    provider=provider,
                    total_cost_usd=total_cost,
                    record_count=len(day_recs),
                    total_tokens_in=total_in,
                    total_tokens_out=total_out,
                ))

        return result

    def reconcile(
        self,
        expected: List[CostRecord],
        actual: List[CostRecord],
    ) -> Dict[str, Any]:
        """对账：比较预期 vs 实际。

        Args:
            expected: LAO 路由日志（预期成本）。
            actual: Provider 账单（实际成本）。

        Returns:
            对账结果字典。
        """
        expected_agg = self.aggregate(expected)
        actual_agg = self.aggregate(actual)

        # 按 (window_start, provider) 建索引
        expected_idx = {(a.window_start, a.provider): a for a in expected_agg}
        actual_idx = {(a.window_start, a.provider): a for a in actual_agg}

        all_keys = set(expected_idx.keys()) | set(actual_idx.keys())
        discrepancies = []

        for key in sorted(all_keys):
            exp = expected_idx.get(key)
            act = actual_idx.get(key)

            if exp is None:
                discrepancies.append({
                    "type": "missing_expected",
                    "window": key[0],
                    "provider": key[1],
                    "actual_cost": act.total_cost_usd if act else 0,
                    "actual_count": act.record_count if act else 0,
                })
            elif act is None:
                discrepancies.append({
                    "type": "missing_actual",
                    "window": key[0],
                    "provider": key[1],
                    "expected_cost": exp.total_cost_usd,
                    "expected_count": exp.record_count,
                })
            else:
                # 比较
                cost_diff = act.total_cost_usd - exp.total_cost_usd
                count_diff = act.record_count - exp.record_count

                if exp.total_cost_usd > 0:
                    cost_diff_pct = abs(cost_diff) / exp.total_cost_usd * 100
                else:
                    cost_diff_pct = 100.0 if cost_diff != 0 else 0.0

                status = "ok"
                if cost_diff_pct >= self._config.block_threshold_pct:
                    status = "blocked"
                elif cost_diff_pct >= self._config.alert_threshold_pct:
                    status = "alert"
                elif count_diff != 0:
                    status = "count_mismatch"

                if status != "ok":
                    discrepancies.append({
                        "type": "mismatch",
                        "window": key[0],
                        "provider": key[1],
                        "expected_cost": exp.total_cost_usd,
                        "actual_cost": act.total_cost_usd,
                        "cost_diff": cost_diff,
                        "cost_diff_pct": round(cost_diff_pct, 2),
                        "expected_count": exp.record_count,
                        "actual_count": act.record_count,
                        "count_diff": count_diff,
                        "status": status,
                    })

        return {
            "expected_total": sum(a.total_cost_usd for a in expected_agg),
            "actual_total": sum(a.total_cost_usd for a in actual_agg),
            "expected_count": sum(a.record_count for a in expected_agg),
            "actual_count": sum(a.record_count for a in actual_agg),
            "discrepancies": discrepancies,
            "status": "blocked" if any(d.get("status") == "blocked" or d.get("type") in ("missing_expected", "missing_actual") for d in discrepancies)
                       else "alert" if discrepancies else "ok",
        }
