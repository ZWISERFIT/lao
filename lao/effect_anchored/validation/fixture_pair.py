# v3.5.1-fix: A1-A3
"""
Regression Replay Fixture Pair — LAO v3.5.1
=============================================

FixturePair: 一对回归重放测试夹具，包含坏路径（应被BLOCK）和有效路径（应PASS）。
FixturePairValidator: 验证夹具对，返回 FixturePairResult（verdict: pass/fail）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class FixturePair:
    """一对回归重放测试夹具。"""
    pair_id: str
    anchor_id: str
    bad_path_context: Any
    valid_path_context: Any
    bad_path_expected: str = "BLOCK"
    valid_path_expected: str = "PASS"
    created_at: str = field(default_factory=_now_iso)


@dataclass
class FixturePairResult:
    """一次夹具对验证结果。"""
    bad_path_result: str      # "BLOCK" | "PASS" | "ERROR"
    valid_path_result: str    # "BLOCK" | "PASS" | "ERROR"
    verdict: str              # "pass" | "fail"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bad_path_result": self.bad_path_result,
            "valid_path_result": self.valid_path_result,
            "verdict": self.verdict,
        }


class FixturePairValidator:
    """验证夹具对: 坏路径应BLOCK，有效路径应PASS。"""

    def validate_pair(self, pair: FixturePair, anchor: Any,
                      route_fn: Callable[[Any], str]) -> FixturePairResult:
        """验证一对夹具。

        Args:
            pair: FixturePair 实例。
            anchor: 锚点对象（供 route_fn 使用）。
            route_fn: 接受 context 返回 "BLOCK"|"PASS" 的调用函数。

        Returns:
            FixturePairResult
        """
        try:
            bad_result = route_fn(pair.bad_path_context)
        except Exception:
            bad_result = "ERROR"

        try:
            valid_result = route_fn(pair.valid_path_context)
        except Exception:
            valid_result = "ERROR"

        verdict = (
            "pass"
            if bad_result == pair.bad_path_expected and valid_result == pair.valid_path_expected
            else "fail"
        )
        return FixturePairResult(
            bad_path_result=bad_result,
            valid_path_result=valid_result,
            verdict=verdict,
        )
