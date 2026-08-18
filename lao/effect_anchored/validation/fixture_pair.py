# v3.5.1-fix: A1-A3
# v3.5.1-glm: A1-A3
"""
Regression Replay Fixture Pair — LAO v3.5.1
=============================================

FixturePair: 一对回归重放测试夹具，包含坏路径（应被BLOCK）和有效路径（应PASS）。
FixturePairValidator: 验证夹具对，返回 FixturePairResult（verdict: pass/fail）。
FixturePairStore: 按 pair_id 索引的夹具对存储，供 Anchor.run_fixture_replay 查找。
replay_pairs: 批量重放夹具对并返回统计结果。
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# A1: 单路径执行超时阈值（秒），超过则结果置 ERROR
_TIMEOUT_SECONDS = 5.0


def _run_route_with_timeout(
    route_fn: Callable[[Any], str], context: Any, timeout: float = _TIMEOUT_SECONDS
) -> tuple:
    """执行单条路径，超时(>timeout秒)或异常时返回 ERROR。

    使用 ThreadPoolExecutor 实现超时保护：若 route_fn 执行超过 timeout 秒，
    future.result() 抛出 TimeoutError，结果置为 "ERROR"。

    Args:
        route_fn: 接受 context 返回 "BLOCK"|"PASS" 的调用函数。
        context: 传给 route_fn 的上下文。
        timeout: 超时阈值（秒），默认 5.0。

    Returns:
        (result_str, elapsed_ms) 二元组。
    """
    start = time.monotonic()
    result = "ERROR"
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(route_fn, context)
        try:
            result = future.result(timeout=timeout)
        except Exception:
            result = "ERROR"
    finally:
        pool.shutdown(wait=False)
    elapsed_ms = int((time.monotonic() - start) * 1000)
    return result, elapsed_ms


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
    bad_path_elapsed_ms: int = 0   # A1: 坏路径执行耗时（毫秒）
    valid_path_elapsed_ms: int = 0 # A1: 有效路径执行耗时（毫秒）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bad_path_result": self.bad_path_result,
            "valid_path_result": self.valid_path_result,
            "verdict": self.verdict,
            "bad_path_elapsed_ms": self.bad_path_elapsed_ms,
            "valid_path_elapsed_ms": self.valid_path_elapsed_ms,
        }


class FixturePairValidator:
    """验证夹具对: 坏路径应BLOCK，有效路径应PASS。"""

    def validate_pair(self, pair: FixturePair, anchor: Any,
                      route_fn: Callable[[Any], str]) -> FixturePairResult:
        """验证一对夹具（A1: 含超时保护与耗时记录）。

        每条路径独立执行，超过 5 秒自动置为 "ERROR"。
        同时记录每条路径的执行耗时（elapsed_ms）。

        Args:
            pair: FixturePair 实例。
            anchor: 锚点对象（供 route_fn 使用）。
            route_fn: 接受 context 返回 "BLOCK"|"PASS" 的调用函数。

        Returns:
            FixturePairResult（含 bad/valid 各自的 result 和 elapsed_ms）。
        """
        bad_result, bad_elapsed = _run_route_with_timeout(
            route_fn, pair.bad_path_context
        )
        valid_result, valid_elapsed = _run_route_with_timeout(
            route_fn, pair.valid_path_context
        )

        verdict = (
            "pass"
            if bad_result == pair.bad_path_expected and valid_result == pair.valid_path_expected
            else "fail"
        )
        return FixturePairResult(
            bad_path_result=bad_result,
            valid_path_result=valid_result,
            verdict=verdict,
            bad_path_elapsed_ms=bad_elapsed,
            valid_path_elapsed_ms=valid_elapsed,
        )


class FixturePairStore:
    """夹具对存储，按 pair_id 索引查找。

    供 Anchor.run_fixture_replay 通过 fixture_pair_id 查找对应 FixturePair。
    """

    def __init__(self) -> None:
        self._pairs: Dict[str, FixturePair] = {}

    def put(self, pair: FixturePair) -> None:
        """注册夹具对到存储。"""
        self._pairs[pair.pair_id] = pair

    def get(self, pair_id: str) -> Optional[FixturePair]:
        """按 pair_id 查找夹具对，未找到返回 None。"""
        return self._pairs.get(pair_id)

    def all_pairs(self) -> List[FixturePair]:
        """返回所有已注册夹具对列表。"""
        return list(self._pairs.values())


# 模块级全局存储实例
fixture_pair_store = FixturePairStore()


def replay_pairs(
    pairs: list, anchor: Any, route_fn: Callable[[Any], str]
) -> Dict[str, Any]:
    """批量重放所有夹具对，返回统计结果（A2）。

    逐对调用 FixturePairValidator.validate_pair 执行验证，汇总 pass/fail/error 计数。
    任一路径结果为 "ERROR" 的夹具对计入 error 而非 pass/fail。

    Args:
        pairs: FixturePair 列表。
        anchor: 锚点对象（供 route_fn 使用）。
        route_fn: 接受 context 返回 "BLOCK"|"PASS" 的调用函数。

    Returns:
        {"results": [...], "pass": n, "fail": n, "error": n, "total": n}
    """
    validator = FixturePairValidator()
    results: List[Dict[str, Any]] = []
    pass_n = 0
    fail_n = 0
    error_n = 0
    for pair in pairs:
        result = validator.validate_pair(pair, anchor, route_fn)
        results.append(result.to_dict())
        has_error = (
            result.bad_path_result == "ERROR"
            or result.valid_path_result == "ERROR"
        )
        if has_error:
            error_n += 1
        elif result.verdict == "pass":
            pass_n += 1
        else:
            fail_n += 1
    return {
        "results": results,
        "pass": pass_n,
        "fail": fail_n,
        "error": error_n,
        "total": len(pairs),
    }
