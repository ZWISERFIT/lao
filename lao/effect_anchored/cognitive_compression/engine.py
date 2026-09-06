"""认知压缩核心引擎（180号 §4.4 裁剪算法 + §4.5 缓存 + §4.6 三道兜底）。

**语境无关**：本模块只接受「工具名列表 + 用户文本」，只返回「保留哪些工具名」。
它不认识 payload、不认识 Cordis ctx、不做任何 I/O（指标落盘在 metrics.py，由适配器
调用）。这是 195号 §3.1「N1 核心引擎必须跨语境复用」的落地方式。

三道兜底（180号 §4.6，原文「兜底 3 是唯一能检测误裁剪的信号，必须诚实回退」）：
    兜底 1  意图判不准（general）           → noop
    兜底 2  裁剪后工具数 < min_tools        → noop(too_aggressive)
    兜底 3  宿主报告「被裁工具被调用」       → 该意图进黑名单，TTL 内不再裁
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .config import CompressionConfig
from .intent import INTENT_GENERAL, ToolIntentClassifier
from .taxonomy import INTENT_DOMAIN_MAP, ToolTaxonomy

#: fallback 原因枚举（进指标，便于聚合告警）
FALLBACK_DISABLED = "disabled"
FALLBACK_TOO_FEW_TOOLS = "too_few_tools"
FALLBACK_INTENT_UNKNOWN = "intent_unknown"
FALLBACK_TOO_AGGRESSIVE = "too_aggressive"
FALLBACK_BLACKLISTED = "blacklisted"
FALLBACK_ERROR = "error"


@dataclass(frozen=True)
class CompressionDecision:
    """一次压缩决策的完整结果（不可变，便于进指标与断言）。

    Attributes:
        intent: 分类结果。
        kept: 保留的工具名（保持输入顺序）。
        dropped: 被裁掉的工具名。
        original_count: 裁剪前工具数。
        fallback: 回退原因；None 表示真的裁了。
        cache_hit: 是否命中决策缓存。
        latency_ms: 决策耗时（毫秒）。
    """

    intent: str
    kept: Tuple[str, ...]
    dropped: Tuple[str, ...]
    original_count: int
    fallback: Optional[str] = None
    cache_hit: bool = False
    latency_ms: float = 0.0

    @property
    def kept_count(self) -> int:
        return len(self.kept)

    @property
    def is_noop(self) -> bool:
        """True = 不要动宿主数据（回退或本来就不需要裁）。"""
        return self.fallback is not None or not self.dropped

    @property
    def ratio(self) -> float:
        """按工具数计的裁剪比例。字符级比例由适配器实测填入指标。"""
        if self.original_count <= 0:
            return 0.0
        return len(self.dropped) / self.original_count


class _DecisionCache:
    """进程内 LRU + TTL 缓存（180号 §4.5：500 条 / 5 分钟 / dict + Lock）。"""

    def __init__(self, max_items: int, ttl_s: int) -> None:
        self._max = max(1, int(max_items))
        self._ttl = max(0, int(ttl_s))
        self._data: "OrderedDict[str, Tuple[float, Tuple[str, ...]]]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Tuple[str, ...]]:
        if self._ttl == 0:
            return None
        now = time.time()
        with self._lock:
            hit = self._data.get(key)
            if hit is None:
                return None
            stored_at, kept = hit
            if now - stored_at > self._ttl:
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return kept

    def put(self, key: str, kept: Tuple[str, ...]) -> None:
        if self._ttl == 0:
            return
        with self._lock:
            self._data[key] = (time.time(), kept)
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


class _IntentBlacklist:
    """误裁黑名单（180号 §4.6 兜底 3：命中意图 TTL 内不再裁剪）。"""

    def __init__(self, ttl_s: int) -> None:
        self._ttl = max(0, int(ttl_s))
        self._until: Dict[str, float] = {}
        self._lock = threading.Lock()

    def block(self, intent: str) -> float:
        """把某意图拉黑，返回解禁时间戳。"""
        with self._lock:
            until = time.time() + self._ttl
            self._until[intent] = until
            return until

    def is_blocked(self, intent: str) -> bool:
        if self._ttl == 0:
            return False
        with self._lock:
            until = self._until.get(intent)
            if until is None:
                return False
            if time.time() >= until:
                self._until.pop(intent, None)
                return False
            return True

    def snapshot(self) -> Dict[str, float]:
        with self._lock:
            return dict(self._until)

    def clear(self) -> None:
        with self._lock:
            self._until.clear()


class CognitiveCompressor:
    """N1 认知压缩决策器。

    线程安全：缓存与黑名单各自持锁；分类器与分类表为只读结构。

    Args:
        config: 运行配置。
        taxonomy: 工具分类器；None 时用默认表。
        classifier: 意图分类器；None 时用默认关键词表。
    """

    def __init__(
        self,
        config: CompressionConfig,
        taxonomy: Optional[ToolTaxonomy] = None,
        classifier: Optional[ToolIntentClassifier] = None,
    ) -> None:
        self.config = config
        self.taxonomy = taxonomy or ToolTaxonomy()
        self.classifier = classifier or ToolIntentClassifier()
        self._cache = _DecisionCache(config.cache_max, config.cache_ttl_s)
        self._blacklist = _IntentBlacklist(config.blacklist_ttl_s)
        self._intent_domains: Dict[str, frozenset] = dict(INTENT_DOMAIN_MAP)
        if config.extra_intent_domains:
            self._intent_domains.update(config.extra_intent_domains)

    # ── 主入口 ────────────────────────────────────────────────────────
    def decide(self, tool_names: Sequence[str], user_text: str) -> CompressionDecision:
        """做出压缩决策。永不抛异常——任何内部错误都回退为 noop。

        Args:
            tool_names: 宿主当前暴露的全部工具名（顺序会被保留）。
            user_text: 用户意图文本。本方法只读，不回传、不修改（红线 C3）。

        Returns:
            CompressionDecision
        """
        started = time.perf_counter()
        names: Tuple[str, ...] = tuple(str(n) for n in (tool_names or ()))

        def _noop(intent: str, reason: Optional[str], cache_hit: bool = False) -> CompressionDecision:
            return CompressionDecision(
                intent=intent,
                kept=names,
                dropped=(),
                original_count=len(names),
                fallback=reason,
                cache_hit=cache_hit,
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )

        try:
            if not self.config.enabled:
                return _noop(INTENT_GENERAL, FALLBACK_DISABLED)
            # 工具本来就少，不动（180号 §4.4 首行守卫）
            if len(names) <= self.config.min_tools:
                return _noop(INTENT_GENERAL, FALLBACK_TOO_FEW_TOOLS)

            intent = self.classifier.classify(user_text)
            # 兜底 1：判不准 → 全保留
            if intent == INTENT_GENERAL or intent not in self._intent_domains:
                return _noop(intent, FALLBACK_INTENT_UNKNOWN)
            # 兜底 3：该意图在误裁黑名单内 → 全保留
            if self._blacklist.is_blocked(intent):
                return _noop(intent, FALLBACK_BLACKLISTED)

            key = self._cache_key(intent, names)
            cached = self._cache.get(key)
            if cached is not None:
                kept = tuple(n for n in names if n in set(cached))
                # 缓存里的名单可能因宿主工具变动而不再齐全，仍按 min_tools 复核
                if len(kept) < self.config.min_tools:
                    return _noop(intent, FALLBACK_TOO_AGGRESSIVE, cache_hit=True)
                return CompressionDecision(
                    intent=intent,
                    kept=kept,
                    dropped=tuple(n for n in names if n not in set(kept)),
                    original_count=len(names),
                    fallback=None,
                    cache_hit=True,
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                )

            kept_domains = self._intent_domains[intent]
            always = self.config.always_keep_domains or frozenset()
            kept_list: List[str] = []
            for n in names:
                domain = self.taxonomy.domain_of(n)
                if domain in kept_domains or domain in always:
                    kept_list.append(n)
                elif domain == "unknown" and self.config.keep_unknown:
                    # 认不出的工具一律保留：源码漂移只降压缩率，不造成误裁
                    kept_list.append(n)
            kept = tuple(kept_list)

            # 兜底 2：剪得太狠 → 全保留
            if len(kept) < self.config.min_tools:
                return _noop(intent, FALLBACK_TOO_AGGRESSIVE)

            self._cache.put(key, kept)
            return CompressionDecision(
                intent=intent,
                kept=kept,
                dropped=tuple(n for n in names if n not in set(kept)),
                original_count=len(names),
                fallback=None,
                cache_hit=False,
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )
        except Exception:
            # fail-open：压缩永不阻塞宿主请求
            return _noop(INTENT_GENERAL, FALLBACK_ERROR)

    # ── 兜底 3 的对外接口 ──────────────────────────────────────────────
    def report_misprune(self, intent: str, tool_name: str = "") -> float:
        """宿主检测到「被裁掉的工具被下游要求调用」时调用。

        180号 §4.6 兜底 3：该意图立即进黑名单（默认 30 分钟），并清掉其缓存决策，
        宿主侧应同时用完整工具集重发。

        Args:
            intent: 当次决策的意图。
            tool_name: 被误裁的工具名（仅进日志，便于分类器纠偏）。

        Returns:
            黑名单解禁时间戳（epoch 秒）。
        """
        self._cache.clear()
        return self._blacklist.block(intent or INTENT_GENERAL)

    def blacklist_snapshot(self) -> Dict[str, float]:
        """当前黑名单 {意图: 解禁时间戳}，供健康检查端点暴露。"""
        return self._blacklist.snapshot()

    def cache_size(self) -> int:
        return len(self._cache)

    def reset(self) -> None:
        """清空缓存与黑名单（仅测试与回滚演练使用）。"""
        self._cache.clear()
        self._blacklist.clear()

    # ── 内部 ──────────────────────────────────────────────────────────
    @staticmethod
    def _cache_key(intent: str, names: Iterable[str]) -> str:
        """180号 §4.5：sha256(intent + 排序后工具名)[:16]。"""
        raw = intent + "|" + ",".join(sorted(str(n) for n in names))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
