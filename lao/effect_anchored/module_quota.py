# v3.5.2-laorefactor: P1-3
"""
Module Quota — LAO per-module 成本配额 (2026-08-19 Shuyu审定·外部案例A[E])
============================================================================

外部案例A(waxell $47k 死循环[E·仅设计参考])映射: per-module 预算上限·
防单模块跑飞。

设计:
    - 每个 LAO 模块(router/extractor/experience_loop/reality_check...)独立配额
    - 模块花费超上限 → check 返回 over_limit → 调用方拦截/降级
    - 全局总和上限(跨模块兜底·防总和失控)

约束: 仅标准库 · docstring · fail-open(异常返回未超限·不阻断)。
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional


class ModuleQuota:
    """LAO per-module 成本配额注册表。"""

    def __init__(self, global_limit: float = 0.0):
        """初始化。

        Args:
            global_limit: 全局总和上限(0=不限·跨模块兜底)。
        """
        self.global_limit = max(global_limit, 0.0)
        self._limits: Dict[str, float] = {}
        self._spent: Dict[str, float] = {}
        self._lock = threading.Lock()

    def register(self, module: str, limit: float) -> None:
        """注册模块配额上限。"""
        with self._lock:
            self._limits[module] = max(limit, 0.0)
            self._spent.setdefault(module, 0.0)

    def check(self, module: str, expected_cost: float = 0.0) -> Dict[str, Any]:
        """检查模块调用是否超配额。

        Returns:
            {"allowed": bool, "module": str, "spent": float, "limit": float,
             "global_spent": float, "global_limit": float, "over_limit": bool}

        fail-open: 未注册模块 → allowed=True(不拦截·向后兼容)。
        """
        try:
            with self._lock:
                limit = self._limits.get(module)
                spent = self._spent.get(module, 0.0)
                global_spent = sum(self._spent.values())
                over = False
                if limit is not None:
                    # 已耗尽(spent>=limit)或本次调用会超 → 拦截
                    if spent >= limit or spent + expected_cost > limit:
                        over = True
                if self.global_limit and global_spent + expected_cost > self.global_limit:
                    over = True
                return {
                    "allowed": not over,
                    "module": module,
                    "spent": round(spent, 4),
                    "limit": limit if limit is not None else -1.0,
                    "global_spent": round(global_spent, 4),
                    "global_limit": self.global_limit,
                    "over_limit": over,
                }
        except Exception:
            return {"allowed": True, "module": module, "spent": 0.0,
                    "limit": -1.0, "global_spent": 0.0,
                    "global_limit": self.global_limit, "over_limit": False}

    def record_spend(self, module: str, cost: float) -> Dict[str, Any]:
        """记录模块花费·返回更新后状态。"""
        try:
            with self._lock:
                if module not in self._spent:
                    self._spent[module] = 0.0
                self._spent[module] += max(cost, 0.0)
                limit = self._limits.get(module)
                return {
                    "module": module,
                    "spent": round(self._spent[module], 4),
                    "limit": limit if limit is not None else -1.0,
                    "over_limit": limit is not None and self._spent[module] > limit,
                }
        except Exception:
            return {"module": module, "spent": 0.0, "limit": -1.0,
                    "over_limit": False}

    def summary(self) -> Dict[str, Any]:
        """各模块花费/配额摘要。"""
        with self._lock:
            return {
                "modules": {m: {"spent": round(self._spent.get(m, 0.0), 4),
                                "limit": self._limits.get(m, -1.0)}
                            for m in set(self._limits) | set(self._spent)},
                "global_spent": round(sum(self._spent.values()), 4),
                "global_limit": self.global_limit,
            }

    # ------------------------------------------------------------------
    # 护栏3(2026-08-19 Stella审核): per-module 配额 = 历史峰值日均 × 150% 裕度
    # (不可设均值·否则误伤峰值→降级→命中率↓)。超大模块单独配额。
    # ------------------------------------------------------------------

    @staticmethod
    def suggest_limit_from_history(avg_daily: float, peak_daily: float) -> float:
        """按历史峰值日均 × 150% 裕度 建议模块配额(护栏3)。

        Args:
            avg_daily: 历史平均日花费(元)。
            peak_daily: 历史峰值日花费(元)。

        Returns:
            建议配额 = max(peak_daily, avg_daily) × 1.5(150% 裕度)。
            peak_daily 不可得(<=0) → 用 avg_daily × 1.5(同裕度)。
        """
        try:
            base = max(avg_daily, peak_daily, 0.0)
            if base <= 0:
                return 0.0
            return round(base * 1.5, 2)
        except (TypeError, ValueError):
            return 0.0
