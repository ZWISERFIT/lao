"""T5 Provider 级命中率统计。

P1-A T5 规格落地（145-T5-Design 第 2.4 节）：
    - ProviderStats: 按 Provider 聚合的命中率统计
    - 滚动窗口：最近 N 条 TokenRecord
    - 最小样本数：10（与 router_r3.py HITRATE_MIN_SAMPLES 对齐）
    - 命中率公式：cache_hit_tokens / (cache_hit_tokens + cache_miss_tokens)

约束：仅标准库 · 零外部依赖
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional

from .record import TokenRecord, VALID_PROVIDERS, compute_hit_rate


# ── 常量 ──────────────────────────────────────────────────────────────

DEFAULT_WINDOW_SIZE = 100  # 滚动窗口最大记录数
MIN_SAMPLES = 10           # 最小样本数（与 router_r3.py HITRATE_MIN_SAMPLES 对齐）


@dataclass
class ProviderStats:
    """单个 Provider 的命中率统计。

    维护一个滚动窗口（最近 N 条 TokenRecord），
    聚合计算 cache_hit_tokens / (cache_hit_tokens + cache_miss_tokens)。
    """

    provider: str
    window_size: int = DEFAULT_WINDOW_SIZE
    _records: Deque[TokenRecord] = field(default_factory=deque, repr=False)

    def __post_init__(self) -> None:
        if self.provider not in VALID_PROVIDERS:
            raise ValueError(
                f"provider 必须为 {sorted(VALID_PROVIDERS)} 之一，实际: {self.provider!r}"
            )
        self._records = deque(maxlen=self.window_size)

    def add_record(self, record: TokenRecord) -> None:
        """添加一条 TokenRecord 到滚动窗口。

        Args:
            record: 标准化 Token 记录。

        Raises:
            ValueError: record.provider 与 self.provider 不匹配。
        """
        if record.provider != self.provider:
            raise ValueError(
                f"record.provider({record.provider!r}) "
                f"!= self.provider({self.provider!r})"
            )
        self._records.append(record)

    @property
    def sample_count(self) -> int:
        """当前窗口内样本数。"""
        return len(self._records)

    @property
    def total_cache_hit(self) -> int:
        """窗口内缓存命中 token 总和。"""
        return sum(r.cache_hit_tokens for r in self._records)

    @property
    def total_cache_miss(self) -> int:
        """窗口内缓存未命中 token 总和。"""
        return sum(r.cache_miss_tokens for r in self._records)

    @property
    def total_input(self) -> int:
        """窗口内输入 token 总和。"""
        return sum(r.input_tokens for r in self._records)

    @property
    def total_output(self) -> int:
        """窗口内输出 token 总和。"""
        return sum(r.output_tokens for r in self._records)

    @property
    def total_cost_yuan(self) -> float:
        """窗口内成本总和（人民币元）。"""
        return sum(r.cost_yuan for r in self._records)

    def hit_rate(self) -> Optional[float]:
        """Provider 级缓存命中率。

        Returns:
            命中率 float [0, 1]；样本不足或分母为 0 时返回 None。
        """
        if self.sample_count < MIN_SAMPLES:
            return None
        return compute_hit_rate(self.total_cache_hit, self.total_cache_miss)

    def summary(self) -> Dict:
        """返回统计摘要字典。"""
        return {
            "provider": self.provider,
            "sample_count": self.sample_count,
            "total_input_tokens": self.total_input,
            "total_output_tokens": self.total_output,
            "total_cache_hit_tokens": self.total_cache_hit,
            "total_cache_miss_tokens": self.total_cache_miss,
            "hit_rate": self.hit_rate(),
            "total_cost_yuan": round(self.total_cost_yuan, 6),
            "min_samples_met": self.sample_count >= MIN_SAMPLES,
        }


class ProviderStatsRegistry:
    """Provider 统计注册表：管理所有 Provider 的 ProviderStats。"""

    def __init__(self, window_size: int = DEFAULT_WINDOW_SIZE) -> None:
        self._window_size = window_size
        self._stats: Dict[str, ProviderStats] = {}
        for p in VALID_PROVIDERS:
            self._stats[p] = ProviderStats(provider=p, window_size=window_size)

    def record(self, token_record: TokenRecord) -> None:
        """记录一条 TokenRecord 到对应 Provider 的统计窗口。

        Args:
            token_record: 标准化 Token 记录。
        """
        stats = self._stats.get(token_record.provider)
        if stats is None:
            return  # 未知 provider，静默跳过
        stats.add_record(token_record)

    def get_stats(self, provider: str) -> Optional[ProviderStats]:
        """获取指定 Provider 的统计对象。"""
        return self._stats.get(provider)

    def get_hit_rate(self, provider: str) -> Optional[float]:
        """获取指定 Provider 的命中率（样本不足返回 None）。"""
        stats = self._stats.get(provider)
        if stats is None:
            return None
        return stats.hit_rate()

    def all_summaries(self) -> Dict[str, Dict]:
        """返回所有 Provider 的统计摘要。"""
        return {p: s.summary() for p, s in self._stats.items()}
