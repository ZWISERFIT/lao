"""
MemoryIntelligence — Memory Intelligence Engine (Phase2 P1-3·创始人令 v3.4)
=============================================================================
第二核心卖点 = Memory Intelligence。ContextLifecycle 已完成, 现在进入 Memory Optimization。

三个区域(创始人):
    Hot Memory       (立即使用): current preference / active task / recent decisions
    Experience Memory(可复用):   verified solutions / successful workflows / patterns
    Archive Memory   (冷存储):   historical conversation / old context

目标: 减少 token / compaction / CPU / latency。
    MEMORY.md 55KB → Active Context 8KB + Experience 15KB + Archive 32KB

新增: MemoryOptimizationEvent(TrustEvent subtype=MemoryEvent):
    before_tokens / after_tokens / compression_ratio / retrieval_accuracy / reuse_count
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# 存储区域
HOT = "hot"
EXPERIENCE = "experience"
ARCHIVE = "archive"
REGIONS = [HOT, EXPERIENCE, ARCHIVE]


@dataclass
class MemoryItem:
    """单条记忆(带区域标签)。"""
    content: str
    region: str = HOT
    tokens: int = 0
    source: str = ""          # attr: preference/decision/verified_solution/... 
    reuse_count: int = 0
    key: str = ""
    ts: str = ""


@dataclass
class MemoryImpact:
    """Memory 优化影响(用户价值)."""
    before_tokens: int = 0
    after_tokens: int = 0
    compression_ratio: float = 0.0
    retrieval_accuracy: float = 1.0     # 0~1
    reuse_count: int = 0
    hot: int = 0
    experience: int = 0
    archive: int = 0

    def to_trust_event(self) -> dict:
        """→ TrustEvent(MemoryEvent)。"""
        return {
            "event": "MemoryOptimization",
            "subtype": "MemoryEvent",
            "before_tokens": self.before_tokens,
            "after_tokens": self.after_tokens,
            "compression_ratio": round(self.compression_ratio, 4),
            "retrieval_accuracy": self.retrieval_accuracy,
            "reuse_count": self.reuse_count,
            "hot": self.hot, "experience": self.experience, "archive": self.archive,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }


class MemoryIntelligenceEngine:
    """Memory 智能分层 + 优化影响计算。"""

    def __init__(self):
        self._items: Dict[str, MemoryItem] = {}
        self._counter = 0

    def put(self, content: str, key: str = "", region: str = HOT,
            source: str = "") -> MemoryItem:
        """存入记忆(默认 Hot·立即使用)。"""
        if not key:
            self._counter += 1
            key = f"mem-{self._counter:03d}"
        item = MemoryItem(content=content, key=key, region=region,
                          tokens=_est_tokens(content), source=source,
                          ts=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        self._items[key] = item
        return item

    def get(self, key: str) -> Optional[MemoryItem]:
        it = self._items.get(key)
        if it:
            it.reuse_count += 1
        return it

    def promote_to_hot(self, key: str) -> Optional[MemoryItem]:
        """复用后提升到 Hot(经验复用·创始人 Test2: 第二次执行自动调 Experience)。"""
        it = self._items.get(key)
        if it and it.region != HOT:
            it.region = HOT
            it.reuse_count += 1
        return it

    # -- 优化: 分层 + 压缩 --
    def optimize(self, source_dict: Dict[str, str]) -> MemoryImpact:
        """把一坨大 memory(如 MEMORY.md 55KB)分层为 Hot/Experience/Archive。"""
        imp = MemoryImpact()
        for key, content in source_dict.items():
            region = self._classify_region(key, content)
            item = self.put(content, key=key, region=region)
            imp.before_tokens += _est_tokens(content)
        # 只把 Hot 的算进"活跃上下文"(减少 token)
        imp.after_tokens = sum(it.tokens for it in self._items.values() if it.region == HOT)
        imp.hot = sum(1 for it in self._items.values() if it.region == HOT)
        imp.experience = sum(1 for it in self._items.values() if it.region == EXPERIENCE)
        imp.archive = sum(1 for it in self._items.values() if it.region == ARCHIVE)
        if imp.before_tokens > 0:
            imp.compression_ratio = 1.0 - imp.after_tokens / imp.before_tokens
        imp.reuse_count = sum(it.reuse_count for it in self._items.values())
        return imp

    def _classify_region(self, key: str, content: str) -> str:
        """启发式分层: 决策/偏好→Hot · 验证解法/流程→Experience · 历史→Archive。"""
        k = key.lower(); c = content.lower()
        if any(w in k for w in ("pref", "decision", "active", "task", "recent")):
            return HOT
        if any(w in k for w in ("verified", "solution", "workflow", "pattern", "experience")) \
           or any(w in c for w in ("已验证", "solution", "verified", "成功")):
            return EXPERIENCE
        if any(w in k for w in ("history", "old", "archive", "log")):
            return ARCHIVE
        # 默认: 超过阈值进 Archive(冷), 否则 Experience
        return ARCHIVE if _est_tokens(content) > 200 else EXPERIENCE

    def region_counts(self) -> Dict[str, int]:
        return {r: sum(1 for it in self._items.values() if it.region == r) for r in REGIONS}


def _est_tokens(s: str) -> int:
    # 粗略估计: ~1.5 字符/token(中文)·英文 ~4字符/token
    return max(1, int(len(s) / 3.5))
