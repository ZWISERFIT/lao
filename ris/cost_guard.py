# v3.5.2-laorefactor: P1-1
"""
RIS Cost Guard — 成本护栏三级化 (2026-08-19 Shuyu审定·外部案例D·Bifrost)
============================================================================

升级单阈值 → 三级渐进响应 (Alert → Throttle → Kill):

    level 0 ( < ALERT_PCT )       : 正常·无动作
    level 1 ( ALERT_PCT~THROTTLE) : Alert 告警(记录·通知)
    level 2 ( THROTTLE~KILL)      : Throttle 节流(限制新调用·降级flash)
    level 3 ( >= KILL_PCT )       : Kill 熔断(停止调用·防跑飞)

默认阈值: 70% 告警 / 90% 节流 / 100% 熔断 (可配)。

外部案例D(Bifrost)映射: 单阈值升级为渐进响应·防成本突然失控。
约束: 仅标准库 · docstring · fail-open(异常返回 level 0·不阻断)。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional


class CostGuard:
    """成本护栏三级化: Alert → Throttle → Kill。"""

    DEFAULT_ALERT_PCT = 70.0       # 70% 告警
    DEFAULT_THROTTLE_PCT = 90.0    # 90% 节流
    DEFAULT_KILL_PCT = 100.0       # 100% 熔断

    def __init__(self, budget_limit: float, alert_pct: float = DEFAULT_ALERT_PCT,
                 throttle_pct: float = DEFAULT_THROTTLE_PCT,
                 kill_pct: float = DEFAULT_KILL_PCT,
                 name: str = "default",
                 kill_modules: Optional[frozenset] = None,
                 degradable: bool = True):
        """初始化成本护栏。

        Args:
            budget_limit: 预算上限(元·period 内)。
            alert_pct/throttle_pct/kill_pct: 三级阈值百分比(默认70/90/100·须实测校准)。
            name: 护栏名称(per-module 隔离·P1-3 支持)。
            kill_modules: 允许 Kill 的模块名集合(护栏1·防止全局熔断)。
                        None = 默认禁止全局 Kill·仅 Throttle/Alert。
            degradable: 是否允许降级(护栏2·heavy/reasoning/code 设 False·宁贵勿错)。
        """
        self.budget_limit = max(budget_limit, 0.01)
        self.alert_pct = alert_pct
        self.throttle_pct = throttle_pct
        self.kill_pct = kill_pct
        self.name = name
        self.kill_modules = kill_modules or frozenset()
        self.degradable = degradable
        self._spent = 0.0
        self._lock = threading.Lock()

    # -- 状态查询 ---------------------------------------------------------

    def _level(self) -> int:
        """当前响应级别(0=正常 1=Alert 2=Throttle 3=Kill)。"""
        if self.budget_limit <= 0:
            return 0
        pct = self._spent / self.budget_limit * 100.0
        if pct >= self.kill_pct:
            return 3
        if pct >= self.throttle_pct:
            return 2
        if pct >= self.alert_pct:
            return 1
        return 0

    def level(self) -> int:
        """线程安全地取当前级别。"""
        with self._lock:
            return self._level()

    def check(self, expected_cost: float = 0.0,
              diagnose: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """检查本次调用是否被允许(基于当前级别+预计成本)。

        Args:
            expected_cost: 本次调用预计成本(元)。
            diagnose: 前置诊断结果(护栏1·P0-1联动)。
                Kill 前必须确认是死循环(root_cause=loop/duplicate)·
                正常峰值流量(root_cause=peak) → 不 Kill·保命中率。

        Returns:
            {"level": 0-3, "allowed": bool, "action": str,
             "spent": float, "budget_limit": float, "pct": float}

            level 0: allowed=True  · action=normal
            level 1: allowed=True  · action=alert(记录)
            level 2: allowed=True  · action=throttle(建议降级flash·限频)
            level 3: allowed=False · action=kill(仅限指定死循环模块·非全局)

        护栏1: Kill 绝不允许全局熔断。仅 name ∈ kill_modules 且
            前置诊断确认为死循环时才 Kill·否则降级为 Throttle。
        护栏2: degradable=False(heavy/reasoning/code) → 不 Throttle 降级。
        """
        try:
            with self._lock:
                lvl = self._level()
                pct = self._spent / self.budget_limit * 100.0 if self.budget_limit else 0.0

            # 护栏1: Kill 仅限指定模块 + 前置诊断确认死循环
            if lvl >= 3:
                is_killable = self.name in self.kill_modules
                is_loop = bool(diagnose and diagnose.get("root_cause") in
                               ("loop", "duplicate", "dead_loop"))
                if not is_killable:
                    # 非 kill_modules → 降级为 Throttle(不拒正常流量·保命中率)
                    return {"level": 2, "allowed": True, "action": "throttle",
                            "spent": round(self._spent, 4),
                            "budget_limit": self.budget_limit, "pct": round(pct, 2),
                            "note": "kill_limited_to_modules"}
                if not is_loop and not diagnose:
                    # 无诊断 → 不能确认死循环 → 降级 Throttle(防误 Kill 正常峰值)
                    return {"level": 2, "allowed": True, "action": "throttle",
                            "spent": round(self._spent, 4),
                            "budget_limit": self.budget_limit, "pct": round(pct, 2),
                            "note": "kill_requires_diagnosis"}
                return {"level": 3, "allowed": False, "action": "kill",
                        "spent": round(self._spent, 4),
                        "budget_limit": self.budget_limit, "pct": round(pct, 2)}

            # 护栏2: degradable=False → 不 Throttle 降级(宁贵勿错·命中率生命线)
            if lvl >= 2:
                if not self.degradable:
                    return {"level": 2, "allowed": True, "action": "alert",
                            "spent": round(self._spent, 4),
                            "budget_limit": self.budget_limit, "pct": round(pct, 2),
                            "note": "non_degradable_tier_throttle_held"}
                return {"level": 2, "allowed": True, "action": "throttle",
                        "spent": round(self._spent, 4),
                        "budget_limit": self.budget_limit, "pct": round(pct, 2)}

            if lvl >= 1:
                return {"level": 1, "allowed": True, "action": "alert",
                        "spent": round(self._spent, 4),
                        "budget_limit": self.budget_limit, "pct": round(pct, 2)}
            return {"level": 0, "allowed": True, "action": "normal",
                    "spent": round(self._spent, 4),
                    "budget_limit": self.budget_limit, "pct": round(pct, 2)}
        except Exception:
            return {"level": 0, "allowed": True, "action": "normal",
                    "spent": 0.0, "budget_limit": self.budget_limit, "pct": 0.0}

    # -- 记账 -------------------------------------------------------------

    def record_spend(self, cost: float) -> Dict[str, Any]:
        """记录实际花费·返回更新后的级别。"""
        try:
            with self._lock:
                self._spent += max(cost, 0.0)
                lvl = self._level()
            return {"level": lvl, "spent": round(self._spent, 4),
                    "pct": round(self._spent / self.budget_limit * 100.0, 2)
                    if self.budget_limit else 0.0}
        except Exception:
            return {"level": 0, "spent": self._spent, "pct": 0.0}

    def reset(self) -> None:
        """重置花费(新周期开始)。"""
        with self._lock:
            self._spent = 0.0
