"""HitRateTracker — L1 命中率追踪 (LAO v3.5)

记录每次路由决策的命中/未命中，按模型维度统计命中率，
并把命中率信号转成经验反馈给 L3(经验确权层)。

口径说明：
    - 与 hit_rate_aggregator.py(离线聚合 lao-router-events.jsonl 报表)互补，
      本模块是运行时内存级追踪器，供 ModelRouter/Runtime 直接调用。
    - 命中率 = hits / (hits + misses)；hits/misses 可按"次"或按 token 数计。
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class RouteDecisionRecord:
    """单次路由决策记录。"""

    model: str
    hit: bool
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    tokens_hit: int = 0
    tokens_miss: int = 0
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelHitStats:
    """单模型命中率统计。"""

    model: str
    hits: int = 0
    misses: int = 0
    tokens_hit: int = 0
    tokens_miss: int = 0

    @property
    def total(self) -> int:
        return self.hits + self.misses

    @property
    def hit_rate(self) -> Optional[float]:
        """按次命中率(无样本返回 None)。"""
        return self.hits / self.total if self.total > 0 else None

    @property
    def token_hit_rate(self) -> Optional[float]:
        """按 token 加权命中率(无缓存 token 返回 None)。"""
        total_tokens = self.tokens_hit + self.tokens_miss
        return self.tokens_hit / total_tokens if total_tokens > 0 else None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "hits": self.hits,
            "misses": self.misses,
            "total": self.total,
            "hit_rate": self.hit_rate,
            "token_hit_rate": self.token_hit_rate,
            "tokens_hit": self.tokens_hit,
            "tokens_miss": self.tokens_miss,
        }


class HitRateTracker:
    """L1 命中率追踪器：按模型维度统计路由命中/未命中。"""

    def __init__(self, min_samples: int = 5):
        """Args:
            min_samples: 低于该样本数的模型不产生 L3 经验反馈(防小样本噪声)。
        """
        self.min_samples = max(1, int(min_samples))
        self._stats: Dict[str, ModelHitStats] = {}
        self._history: List[RouteDecisionRecord] = []

    # ── 记录 ─────────────────────────────────────────────────────────────

    def record(
        self,
        model: str,
        hit: bool,
        tokens_hit: int = 0,
        tokens_miss: int = 0,
        meta: Optional[Dict[str, Any]] = None,
    ) -> RouteDecisionRecord:
        """记录一次路由决策的命中/未命中。"""
        if model not in self._stats:
            self._stats[model] = ModelHitStats(model=model)
        stats = self._stats[model]
        if hit:
            stats.hits += 1
        else:
            stats.misses += 1
        stats.tokens_hit += int(tokens_hit)
        stats.tokens_miss += int(tokens_miss)

        rec = RouteDecisionRecord(
            model=model, hit=bool(hit),
            tokens_hit=int(tokens_hit), tokens_miss=int(tokens_miss),
            meta=dict(meta or {}),
        )
        self._history.append(rec)
        return rec

    def record_hit(self, model: str, **kwargs) -> RouteDecisionRecord:
        """记录一次命中。"""
        return self.record(model, True, **kwargs)

    def record_miss(self, model: str, **kwargs) -> RouteDecisionRecord:
        """记录一次未命中。"""
        return self.record(model, False, **kwargs)

    # ── 统计 ─────────────────────────────────────────────────────────────

    def stats_for(self, model: str) -> ModelHitStats:
        """取单模型统计(无记录返回零值统计)。"""
        return self._stats.get(model, ModelHitStats(model=model))

    def hit_rate(self, model: Optional[str] = None) -> Optional[float]:
        """命中率：指定模型或全局(按次)。"""
        if model is not None:
            return self.stats_for(model).hit_rate
        hits = sum(s.hits for s in self._stats.values())
        total = sum(s.total for s in self._stats.values())
        return hits / total if total > 0 else None

    def all_stats(self) -> Dict[str, Dict[str, Any]]:
        """按模型维度输出全部统计。"""
        return {m: s.to_dict() for m, s in sorted(self._stats.items())}

    def history(self) -> List[RouteDecisionRecord]:
        """原始决策记录(时间序)。"""
        return list(self._history)

    # ── 命中率 → 经验反馈给 L3 ───────────────────────────────────────────

    def to_experience_feedback(self) -> List[Dict[str, Any]]:
        """把命中率统计转成 L3 经验反馈条目。

        仅样本量 ≥ min_samples 的模型产出反馈：
          - 高命中(≥0.8)  → positive 经验(该模型路由参数稳定, 可沉淀)
          - 低命中(<0.5)  → negative 经验(参数/上下文问题, 建议约束)
          - 其余          → neutral 观测记录
        """
        feedback: List[Dict[str, Any]] = []
        ts = datetime.now(timezone.utc).isoformat()
        for model, stats in sorted(self._stats.items()):
            if stats.total < self.min_samples:
                continue
            rate = stats.hit_rate or 0.0
            if rate >= 0.8:
                verdict = "positive"
            elif rate < 0.5:
                verdict = "negative"
            else:
                verdict = "neutral"
            feedback.append({
                "type": "hit_rate_signal",
                "layer": "L1",
                "model": model,
                "hit_rate": round(rate, 4),
                "samples": stats.total,
                "tokens_hit": stats.tokens_hit,
                "tokens_miss": stats.tokens_miss,
                "verdict": verdict,
                "suggestion": (
                    f"模型 {model} 命中率 {rate:.1%}({stats.total} 样本)"
                    + ("；路由参数稳定，可沉淀为正向经验" if verdict == "positive"
                       else "；建议检查事件参数完整性/上下文稳定性" if verdict == "negative"
                       else "；持续观测")
                ),
                "generated_at": ts,
            })
        return feedback
