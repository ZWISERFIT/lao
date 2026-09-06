"""T7 止损规则 — 配置与监控。

P1-A T7 规格落地（147-T7-Design）：
    - StopLossConfig: 止损阈值配置（与 router_r3.py 第 66-68 行对齐）
    - StopLossMonitor: 运行时止损监控

约束：仅标准库 · 零外部依赖
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, Optional


# ── 默认阈值（与 router_r3.py 第 66-68 行对齐） ────────────────────

DEFAULT_ALERT_TOKENS = 30_000_000    # 30M 告警
DEFAULT_HARD_TOKENS = 50_000_000     # 50M 熔断
DEFAULT_MAX_RETRIES = 5              # 重试上限
DEFAULT_RETRY_WINDOW_SEC = 600       # 10 分钟重试窗口


@dataclass(frozen=True)
class StopLossConfig:
    """止损阈值配置（不可变）。

    字段与 router_r3.py 第 66-68 行环境变量对齐：
        R3_TASK_ALERT_TOKENS = 30000000
        R3_TASK_HARD_TOKENS  = 50000000
        R3_MAX_RETRIES       = 5
    """

    alert_tokens: int = DEFAULT_ALERT_TOKENS
    hard_tokens: int = DEFAULT_HARD_TOKENS
    max_retries: int = DEFAULT_MAX_RETRIES
    retry_window_sec: int = DEFAULT_RETRY_WINDOW_SEC

    def validate(self) -> list:
        """校验配置合法性。"""
        errors = []
        if self.alert_tokens <= 0:
            errors.append("alert_tokens 必须为正整数")
        if self.hard_tokens <= 0:
            errors.append("hard_tokens 必须为正整数")
        if self.hard_tokens < self.alert_tokens:
            errors.append("hard_tokens 必须 >= alert_tokens")
        if self.max_retries < 0:
            errors.append("max_retries 不得为负")
        if self.retry_window_sec <= 0:
            errors.append("retry_window_sec 必须为正整数")
        return errors

    def to_dict(self) -> Dict:
        return {
            "alert_tokens": self.alert_tokens,
            "hard_tokens": self.hard_tokens,
            "max_retries": self.max_retries,
            "retry_window_sec": self.retry_window_sec,
        }


class StopLossMonitor:
    """运行时止损监控器。

    跟踪单个任务的 token 消耗和重试次数，
    在越限时产出告警/熔断判定。

    合成验证说明：
        本类为纯逻辑层，不涉及任何真实运行时数据。
    """

    def __init__(self, config: Optional[StopLossConfig] = None) -> None:
        self._config = config or StopLossConfig()
        # task_id → {"tokens": int, "alerted": bool, "broken": bool,
        #             "retry_count": int, "retry_first_ts": float}
        self._tasks: Dict[str, Dict] = {}

    @property
    def config(self) -> StopLossConfig:
        return self._config

    def add_tokens(self, task_id: str, tokens: int) -> Dict:
        """累计任务 token 并返回状态。

        Args:
            task_id: 任务 ID。
            tokens: 本次消耗 token 数。

        Returns:
            状态字典 {"tokens", "alerted", "broken", "new_alert"}。
        """
        if tokens <= 0:
            return self._get_task_state(task_id)

        t = self._tasks.setdefault(
            task_id,
            {"tokens": 0, "alerted": False, "broken": False,
             "retry_count": 0, "retry_first_ts": 0.0},
        )
        t["tokens"] += tokens
        new_alert = False

        if not t["alerted"] and t["tokens"] >= self._config.alert_tokens:
            t["alerted"] = True
            new_alert = True
        if not t["broken"] and t["tokens"] >= self._config.hard_tokens:
            t["broken"] = True

        t["new_alert"] = new_alert
        return {
            "tokens": t["tokens"],
            "alerted": t["alerted"],
            "broken": t["broken"],
            "new_alert": new_alert,
        }

    def check_blocked(self, task_id: str) -> bool:
        """查询任务是否被熔断阻断。"""
        t = self._tasks.get(task_id)
        if t is None:
            return False
        return bool(t.get("broken", False))

    def record_retry(self, task_id: str, current_time: float) -> Dict:
        """记录一次重试。

        Args:
            task_id: 任务 ID。
            current_time: 当前时间戳（秒）。

        Returns:
            状态字典 {"retry_count", "exceeded", "within_window"}。
        """
        import time as _time
        t = self._tasks.setdefault(
            task_id,
            {"tokens": 0, "alerted": False, "broken": False,
             "retry_count": 0, "retry_first_ts": 0.0},
        )
        first_ts = t.get("retry_first_ts", 0.0)
        # 窗口过期则重置计数
        if first_ts == 0.0 or (current_time - first_ts) > self._config.retry_window_sec:
            t["retry_count"] = 1
            t["retry_first_ts"] = current_time
        else:
            t["retry_count"] = t.get("retry_count", 0) + 1

        return {
            "retry_count": t["retry_count"],
            "exceeded": t["retry_count"] > self._config.max_retries,
            "within_window": True,
        }

    def check_retry_blocked(self, task_id: str) -> bool:
        """查询任务是否因重试超限被阻断。"""
        t = self._tasks.get(task_id)
        if t is None:
            return False
        return t.get("retry_count", 0) > self._config.max_retries

    def get_task_state(self, task_id: str) -> Optional[Dict]:
        """查询任务止损状态。"""
        return self._get_task_state(task_id)

    def _get_task_state(self, task_id: str) -> Dict:
        t = self._tasks.get(task_id)
        if t is None:
            return {"tokens": 0, "alerted": False, "broken": False,
                    "retry_count": 0}
        return {
            "tokens": t.get("tokens", 0),
            "alerted": t.get("alerted", False),
            "broken": t.get("broken", False),
            "retry_count": t.get("retry_count", 0),
        }
