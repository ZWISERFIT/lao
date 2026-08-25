"""FlywheelTracker — L3 飞轮引擎追踪 (LAO v3.5)

飞轮效应链路：
    更多参数 → 更高命中率 → 更多经验 → 更低 Token 消耗
    → 更多用户分享 → 更活跃交易 → (回到)更多参数

追踪指标：
    - 参数覆盖率(param_coverage): EventChecker 检查通过率
    - 命中率提升幅度(hit_rate_lift): 当前周期 vs 基线命中率
    - Token 节省量(tokens_saved): 经验复用/缓存命中所省 token
    - 经验交易活跃度(trade_activity): 周期内确权经验交易数
    - 用户分享增长率(share_growth): 周期间分享数环比

health_report() 输出飞轮健康报告(五指标 + 总分 + 状态)。
周期模型：current/previous 两个计数桶，roll_period() 滚动切换，
环比类指标(命中率提升/分享增长)由此得出。
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class _PeriodCounters:
    """单周期计数桶。"""

    param_checks_total: int = 0
    param_checks_passed: int = 0
    route_hits: int = 0
    route_misses: int = 0
    tokens_saved: int = 0
    trades: int = 0
    shares: int = 0
    started_at: str = field(default_factory=_utcnow)

    @property
    def param_coverage(self) -> Optional[float]:
        return (self.param_checks_passed / self.param_checks_total
                if self.param_checks_total > 0 else None)

    @property
    def hit_rate(self) -> Optional[float]:
        total = self.route_hits + self.route_misses
        return self.route_hits / total if total > 0 else None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "param_checks_total": self.param_checks_total,
            "param_checks_passed": self.param_checks_passed,
            "route_hits": self.route_hits,
            "route_misses": self.route_misses,
            "tokens_saved": self.tokens_saved,
            "trades": self.trades,
            "shares": self.shares,
            "started_at": self.started_at,
            "param_coverage": self.param_coverage,
            "hit_rate": self.hit_rate,
        }


class FlywheelTracker:
    """L3 飞轮指标追踪器。"""

    def __init__(self):
        self._current = _PeriodCounters()
        self._previous: Optional[_PeriodCounters] = None
        self._baseline_hit_rate: Optional[float] = None
        self._events: List[Dict[str, Any]] = []

    # ── 记录接口 ─────────────────────────────────────────────────────────

    def record_param_check(self, passed: bool, total_checks: int = 1) -> None:
        """记录 EventChecker 参数检查结果(参数覆盖率口径)。"""
        n = max(1, int(total_checks))
        self._current.param_checks_total += n
        self._current.param_checks_passed += n if passed else 0
        self._events.append({"type": "param_check", "passed": bool(passed),
                             "n": n, "ts": _utcnow()})

    def record_route(self, hit: bool) -> None:
        """记录一次路由命中/未命中(命中率口径)。"""
        if hit:
            self._current.route_hits += 1
        else:
            self._current.route_misses += 1
        self._events.append({"type": "route", "hit": bool(hit), "ts": _utcnow()})

    def record_tokens_saved(self, tokens: int) -> None:
        """记录 Token 节省量。"""
        self._current.tokens_saved += max(0, int(tokens))
        self._events.append({"type": "tokens_saved", "tokens": int(tokens),
                             "ts": _utcnow()})

    def record_trade(self, count: int = 1) -> None:
        """记录经验交易(确权经验上平台成交)。"""
        self._current.trades += max(0, int(count))
        self._events.append({"type": "trade", "count": int(count), "ts": _utcnow()})

    def record_share(self, count: int = 1) -> None:
        """记录用户分享经验。"""
        self._current.shares += max(0, int(count))
        self._events.append({"type": "share", "count": int(count), "ts": _utcnow()})

    # ── 周期管理 ─────────────────────────────────────────────────────────

    def set_baseline_hit_rate(self, rate: float) -> None:
        """设定命中率基线(无基线时用上一周期命中率)。"""
        self._baseline_hit_rate = max(0.0, min(1.0, float(rate)))

    def roll_period(self) -> None:
        """滚动周期：current → previous，current 清零(环比依据)。"""
        self._previous = self._current
        self._current = _PeriodCounters()

    # ── 指标读取 ─────────────────────────────────────────────────────────

    @property
    def param_coverage(self) -> Optional[float]:
        """参数覆盖率(当前周期)。"""
        return self._current.param_coverage

    @property
    def hit_rate(self) -> Optional[float]:
        """当前周期命中率。"""
        return self._current.hit_rate

    @property
    def hit_rate_lift(self) -> Optional[float]:
        """命中率提升幅度 = 当前 - 基线(基线缺省用上一周期)。"""
        cur = self._current.hit_rate
        if cur is None:
            return None
        baseline = self._baseline_hit_rate
        if baseline is None:
            baseline = self._previous.hit_rate if self._previous else None
        if baseline is None:
            return None
        return cur - baseline

    @property
    def tokens_saved(self) -> int:
        """当前周期 Token 节省量。"""
        return self._current.tokens_saved

    @property
    def trade_activity(self) -> int:
        """当前周期经验交易数。"""
        return self._current.trades

    @property
    def share_growth(self) -> Optional[float]:
        """用户分享增长率(环比上一周期；无上期返回 None)。"""
        if self._previous is None or self._previous.shares <= 0:
            return None
        return (self._current.shares - self._previous.shares) / self._previous.shares

    # ── 飞轮健康报告 ─────────────────────────────────────────────────────

    def health_report(self) -> Dict[str, Any]:
        """输出飞轮健康报告。

        五项指标归一化到 0-1 后加权求总分：
          参数覆盖率 0.25 / 命中率提升 0.25 / Token 节省 0.15 /
          交易活跃度 0.15 / 分享增长 0.20
        状态判定：总分 ≥0.7 HEALTHY；≥0.4 WARMING；否则 STALLING。
        """
        metrics = {
            "param_coverage": self.param_coverage,
            "hit_rate_lift": self.hit_rate_lift,
            "tokens_saved": self.tokens_saved,
            "trades": self.trade_activity,
            "shares": self._current.shares,
        }

        def _norm_coverage(v):
            return v if v is not None else 0.0

        def _norm_lift(v):
            return max(0.0, min(1.0, v + 0.5) * 2.0) if v is not None else 0.0

        def _norm_tokens(v):
            return min(1.0, v / 1_000_000) if v else 0.0  # 1M token 封顶

        def _norm_count(v):
            return min(1.0, v / 100) if v else 0.0        # 100 次/期封顶

        def _norm_growth(v):
            return max(0.0, min(1.0, v)) if v is not None else 0.0

        score = (
            0.25 * _norm_coverage(metrics["param_coverage"])
            + 0.25 * _norm_lift(metrics["hit_rate_lift"])
            + 0.15 * _norm_tokens(metrics["tokens_saved"])
            + 0.15 * _norm_count(metrics["trades"])
            + 0.20 * _norm_growth(self.share_growth)
        )
        if score >= 0.7:
            status = "HEALTHY"
        elif score >= 0.4:
            status = "WARMING"
        else:
            status = "STALLING"

        return {
            "generated_at": _utcnow(),
            "metrics": metrics,
            "score": round(score, 4),
            "status": status,
            "period": {
                "current": self._current.to_dict(),
                "previous": self._previous.to_dict() if self._previous else None,
            },
            "baseline_hit_rate": self._baseline_hit_rate,
        }
