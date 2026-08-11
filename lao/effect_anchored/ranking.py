"""
Retrieval Ranking — LAO v3.1 P1-8
===================================

认知系统的检索排列引擎(开源接口, 不开源权重)。

设计(对齐创始人开源策略):
  - ranking.py 是**开源接口**: 对外提供 retrieval_rank(experiences, query) 等稳定 API
  - 排序**权重来自 weights.json**(创始人认知系统核心) → **不开源**
  - 本文件只定义「权重如何加载 + 如何应用」的公开接口, 权重值本身在 weights.json
  - weights.json 不入库(见 .gitignore) → 开源仓库看不到真实权重

用法:
  ranker = RankingEngine(weights_path=...)   # 加载 weights.json(私有)
  ranked = ranker.retrieve_rank(experiences, query)  # 返回排序后结果
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_WEIGHTS = os.path.join(
    os.path.dirname(__file__), "weights.json"
)

# 开源接口暴露的默认维度(权重值在 weights.json 中)
DEFAULT_DIMENSIONS = ["trust", "freshness", "relevance", "rarity", "trigger"]


class RankingEngine:
    """检索排列引擎(开源接口)。

    权重从 weights.json 加载(私有·不开源)。
    若 weights.json 缺失, 回退到中性默认(均权), 保证接口可用但无认知偏置。
    """

    def __init__(self, weights_path: Optional[str] = None):
        self.weights_path = weights_path or DEFAULT_WEIGHTS
        self.weights: Dict[str, float] = self._load_weights()

    def _load_weights(self) -> Dict[str, float]:
        """从 weights.json 加载权重(不开源·创始人认知系统)。
        缺失/损坏 → 中性默认(均权), 接口不塌。
        """
        try:
            with open(self.weights_path) as f:
                data = json.load(f)
            w = data.get("weights", {})
            if isinstance(w, dict) and w:
                return {k: float(v) for k, v in w.items()}
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
        # 中性均权回退(无认知偏置)
        n = len(DEFAULT_DIMENSIONS)
        return {d: 1.0 / n for d in DEFAULT_DIMENSIONS}

    def weights_signature(self) -> str:
        """(公开) 权重配置的版本/签名标识 —— 不泄露权重值本身。"""
        import hashlib
        return hashlib.sha256(
            json.dumps(self.weights, sort_keys=True).encode()
        ).hexdigest()[:12]

    def retrieve_rank(self, experiences: List[Dict[str, Any]],
                      query: str = "") -> List[Dict[str, Any]]:
        """(公开) 对经验列表做检索排列, 返回降序结果(含 score)。

        每维得分由经验自身属性算出, 再用 weights.json 的权重加权求和。
        """
        scored = []
        for ex in experiences:
            scores = self._dimension_scores(ex, query)
            score = sum(scores.get(d, 0.0) * self.weights.get(d, 0.0)
                        for d in DEFAULT_DIMENSIONS)
            item = dict(ex)
            item["_score"] = round(score, 4)
            item["_dimensions"] = scores
            scored.append(item)
        scored.sort(key=lambda x: x["_score"], reverse=True)
        return scored

    def _dimension_scores(self, ex: Dict[str, Any], query: str) -> Dict[str, float]:
        """计算各维度得分(0-1)。"""
        value = ex.get("value") if isinstance(ex.get("value"), dict) else {}
        trust = float(ex.get("trust_weight") or ex.get("confidence") or 0.5)
        # relevance: query 命中 rule/内容
        rule = str(value.get("rule") or ex.get("rule") or "")
        relevance = 1.0 if (query and query in rule) else 0.3
        return {
            "trust": min(1.0, trust),
            "freshness": 1.0,   # 由调用方填充或默认
            "relevance": relevance,
            "rarity": float(ex.get("rarity", 0.5)),
            "trigger": min(1.0, float(ex.get("trigger_count", 0)) / 10.0),
        }
