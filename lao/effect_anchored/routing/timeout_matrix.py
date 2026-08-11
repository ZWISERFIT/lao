"""
Timeout Matrix — LAO v3.1 P1-15 配套
======================================

加载 timeout_matrix.json 的软硬双阈值, 提供判断:
  - soft_timeout: 超过 → L1 标记"慢"·下次优先避开
  - hard_timeout: 超过 → 立即 fallback 到备用 model

验证需求:
  - 翻译(translation) 500ms 软标记·2500ms+ 硬切
  - 代码审查(code_review) 3000ms 正常不切
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

DEFAULT_MATRIX = os.path.join(os.path.dirname(__file__), "timeout_matrix.json")


class TimeoutMatrix:
    """分模式软硬超时矩阵。"""

    def __init__(self, matrix_path: Optional[str] = None, fallback_threshold_ms: int = 10000):
        self.matrix_path = matrix_path or DEFAULT_MATRIX
        self.fallback_threshold_ms = fallback_threshold_ms
        self.modes: Dict[str, Dict[str, int]] = self._load()

    def _load(self) -> Dict[str, Dict[str, int]]:
        try:
            with open(self.matrix_path) as f:
                data = json.load(f)
            return data.get("modes", {})
        except (OSError, json.JSONDecodeError, TypeError):
            return {}

    def thresholds(self, mode: str) -> Dict[str, int]:
        """取某模式的软硬阈值(未知模式用 fallback)。"""
        return self.modes.get(mode, {
            "hard_timeout_ms": self.fallback_threshold_ms,
            "soft_timeout_ms": self.fallback_threshold_ms // 5,
        })

    def judge(self, mode: str, elapsed_ms: float) -> Dict[str, Any]:
        """判断某次调用是否触发软/硬超时。

        Returns:
            {"mode", "elapsed_ms", "soft_timeout", "hard_timeout",
             "action": "ok"|"slow"|"fallback"}
        """
        th = self.thresholds(mode)
        hard = th["hard_timeout_ms"]
        soft = th["soft_timeout_ms"]
        if elapsed_ms > hard:
            action = "fallback"      # 立即切备用model
        elif elapsed_ms > soft:
            action = "slow"          # 标记慢·下次优先避开
        else:
            action = "ok"
        return {
            "mode": mode, "elapsed_ms": round(elapsed_ms, 1),
            "soft_timeout": soft, "hard_timeout": hard, "action": action,
        }
