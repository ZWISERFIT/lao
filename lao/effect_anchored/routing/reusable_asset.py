"""ReusableAsset — 可复用资产模块 (207号件·加固一)

存储和检索可复用的经验资产：
  - 每次路由成功/失败产生的经验模式
  - 确权后的经验锚点
  - 约束规则产生的防护模式

路由前查询：命中可复用资产 → 直接返回 → 不进 LLM（降本核心）。

与 ExperienceLoop 的关系：
  - ExperienceLoop 负责 L1↔L2↔L3 确权链
  - ReusableAsset 是确权产物的「快速检索层」，供路由前短路
  - 确权成功后自动同步到 ReusableAsset
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


DEFAULT_STORE_PATH = os.environ.get(
    "LAO_REUSABLE_ASSET_PATH",
    os.path.join(os.path.expanduser("~"), ".lao", "reusable_assets.json"),
)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(text: str) -> str:
    """内容指纹：用于去重和快速匹配。"""
    normalized = " ".join(text.lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


@dataclass
class ReusableAsset:
    """单条可复用资产。"""

    asset_id: str
    fingerprint: str
    pattern: str               # 触发模式描述
    solution: str              # 解决方案/答案
    category: str              # routing | constraint | experience | evolution
    confidence: float = 0.5    # 置信度 (0-1)
    usage_count: int = 0       # 被复用次数
    source_anchor_id: str = "" # 来源锚点 ID（可溯源到 ExperienceLoop）
    source_event_id: str = ""  # 来源事件 ID
    created_at: str = field(default_factory=_utc)
    updated_at: str = field(default_factory=_utc)
    last_used_at: str = ""
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ReusableAsset":
        d = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**d)


class ReusableAssetStore:
    """可复用资产存储与检索。"""

    def __init__(self, store_path: Optional[str] = None):
        self.store_path = store_path or DEFAULT_STORE_PATH
        os.makedirs(os.path.dirname(self.store_path) or ".", exist_ok=True)
        self._assets: Dict[str, ReusableAsset] = {}
        self._load()

    # ── 持久化 ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not os.path.exists(self.store_path):
            self._assets = {}
            return
        try:
            with open(self.store_path, encoding="utf-8") as f:
                raw = json.load(f)
            items = raw.get("assets", raw) if isinstance(raw, dict) else raw
            self._assets = {a["asset_id"]: ReusableAsset.from_dict(a)
                            for a in items if isinstance(a, dict) and "asset_id" in a}
        except Exception:
            self._assets = {}

    def _save(self) -> None:
        tmp = self.store_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"schema_version": 1, "updated_at": _utc(),
                        "assets": [a.to_dict() for a in self._assets.values()]},
                       f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.store_path)

    # ── 注册 ────────────────────────────────────────────────────────────

    def register(self, pattern: str, solution: str, category: str = "experience",
                 confidence: float = 0.5, source_anchor_id: str = "",
                 source_event_id: str = "", tags: Optional[List[str]] = None) -> ReusableAsset:
        """注册一条可复用资产。幂等：同 fingerprint 更新而非重复创建。"""
        fp = _fingerprint(pattern)
        # 幂等：已有同 fingerprint → 更新
        for a in self._assets.values():
            if a.fingerprint == fp:
                a.confidence = max(a.confidence, confidence)
                a.usage_count = a.usage_count  # 保持
                a.updated_at = _utc()
                if solution:
                    a.solution = solution
                self._save()
                return a
        asset_id = f"RA-{len(self._assets)+1:04d}"
        asset = ReusableAsset(
            asset_id=asset_id, fingerprint=fp,
            pattern=pattern, solution=solution, category=category,
            confidence=confidence,
            source_anchor_id=source_anchor_id,
            source_event_id=source_event_id,
            tags=list(tags or []),
        )
        self._assets[asset_id] = asset
        self._save()
        return asset

    def register_from_route_result(self, provider: str, model: str, ok: bool,
                                   error: str = "", task_text: str = "",
                                   answer: str = "") -> Optional[ReusableAsset]:
        """从路由结果自动注册可复用资产。

        - 成功路由 + 有 task_text + 有 answer → 注册为 experience 资产
        - 失败路由 + 有 error → 注册为 constraint 资产（防护模式）
        """
        if ok and task_text and answer:
            return self.register(
                pattern=task_text[:500], solution=answer[:2000],
                category="experience", confidence=0.6,
                tags=[f"provider:{provider}", f"model:{model}"],
            )
        if not ok and error:
            return self.register(
                pattern=f"ERROR:{error[:200]}", solution=f"AVOID:{provider}/{model}",
                category="constraint", confidence=0.7,
                tags=[f"error_source:{provider}/{model}"],
            )
        return None

    def register_from_constraint(self, constraint_id: str, error_pattern: str,
                                 solution: str, category: str = "evolution") -> ReusableAsset:
        """从 evolution 约束链注册可复用资产（加固二→加固一桥接）。"""
        return self.register(
            pattern=error_pattern, solution=solution,
            category=category, confidence=0.8,
            source_event_id=constraint_id,
            tags=[f"constraint:{constraint_id}", "evolution"],
        )

    # ── 检索 ────────────────────────────────────────────────────────────

    def query(self, task_text: str, min_confidence: float = 0.6,
              limit: int = 5) -> List[Dict[str, Any]]:
        """查询匹配的可复用资产。

        匹配策略：
          1. 精确指纹匹配（同 pattern → 100% 匹配）
          2. 关键词重叠度匹配（task_text 关键词 vs pattern 关键词）
        按 confidence × relevance 降序返回。
        """
        if not task_text or not self._assets:
            return []

        query_fp = _fingerprint(task_text)
        query_tokens = set(task_text.lower().split())
        results = []

        for a in self._assets.values():
            if a.confidence < min_confidence:
                continue
            # 精确指纹匹配
            if a.fingerprint == query_fp:
                results.append({**a.to_dict(), "_relevance": 1.0})
                continue
            # 关键词重叠度
            pattern_tokens = set(a.pattern.lower().split())
            if not pattern_tokens:
                continue
            overlap = len(query_tokens & pattern_tokens)
            if overlap == 0:
                continue
            # 中文 bigram 匹配
            q_bigrams = {task_text[i:i+2] for i in range(len(task_text)-1)
                         if task_text[i:i+2].strip()}
            p_bigrams = {a.pattern[i:i+2] for i in range(len(a.pattern)-1)
                         if a.pattern[i:i+2].strip()}
            bg_overlap = len(q_bigrams & p_bigrams)
            relevance = (overlap / max(len(query_tokens), 1) +
                         bg_overlap / max(len(q_bigrams), 1)) / 2
            if relevance > 0.1:
                results.append({**a.to_dict(), "_relevance": round(relevance, 4)})

        results.sort(key=lambda r: r["confidence"] * r.get("_relevance", 0), reverse=True)
        return results[:limit]

    def query_best(self, task_text: str, min_confidence: float = 0.7
                   ) -> Optional[Dict[str, Any]]:
        """查询最佳匹配（单条）。路由前短路专用。"""
        matches = self.query(task_text, min_confidence=min_confidence, limit=1)
        return matches[0] if matches else None

    def mark_used(self, asset_id: str) -> None:
        """标记资产被使用（usage_count +1）。"""
        asset = self._assets.get(asset_id)
        if asset:
            asset.usage_count += 1
            asset.last_used_at = _utc()
            self._save()

    # ── 同步 ────────────────────────────────────────────────────────────

    def sync_from_experience_loop(self, loop) -> int:
        """从 ExperienceLoop 确权产物同步可复用资产。

        读取 anchor_store 中高 trust_weight 的锚点 → 注册为可复用资产。
        """
        n = 0
        try:
            anchors = loop.anchor_store.lookup()[:100]
            for a in anchors:
                tw = float(a.get("trust_weight", 0) or 0)
                if tw < 0.7:
                    continue
                aid = str(a.get("anchor_id", ""))
                if not aid:
                    continue
                # 检查是否已同步（按 source_anchor_id 查）
                already = any(x.source_anchor_id == aid
                              for x in self._assets.values())
                if already:
                    continue
                value = a.get("value", {})
                if isinstance(value, dict):
                    pattern = str(value.get("trigger_condition") or
                                  value.get("principle") or aid)
                    solution = str(value.get("action_rule") or
                                   value.get("principle") or "")
                else:
                    pattern = str(value)[:500]
                    solution = ""
                self.register(
                    pattern=pattern, solution=solution,
                    category="experience", confidence=tw,
                    source_anchor_id=aid,
                    tags=list(a.get("tags", [])),
                )
                n += 1
        except Exception:
            pass
        return n

    # ── 运维 ────────────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        by_cat: Dict[str, int] = {}
        total_usage = 0
        for a in self._assets.values():
            by_cat[a.category] = by_cat.get(a.category, 0) + 1
            total_usage += a.usage_count
        return {
            "total": len(self._assets),
            "by_category": by_cat,
            "total_usage": total_usage,
            "store_path": self.store_path,
        }
