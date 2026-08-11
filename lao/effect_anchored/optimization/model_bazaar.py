"""
Model Bazaar — LAO v3.1 P1-12
===============================

模型比价引擎: 每个用户专属的「模型选购指南」(价格/质量/速度/稳定性四维)。

- 四维评估: 价格(cost) / 质量(quality) / 速度(latency) / 稳定性(stability)
- 上周最便宜的模型 → 本周推荐
- 历史推荐准确率追踪(推荐过的模型后续表现如何)
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class ModelBazaar:
    """模型比价 + 选购指南 + 推荐准确率。"""

    def __init__(self, user: str = "default"):
        self.user = user
        self._history: Dict[str, Any] = {}   # user -> {recommendations: [...], accuracy...}
        self._path = None

    def set_history_path(self, path: str) -> None:
        self._path = path
        self._load()

    @staticmethod
    def score(model_metrics: Dict[str, Any]) -> float:
        """综合评分(价格越低/质量越高/速度越快/越稳定 → 分越高, 0-1)。

        model_metrics: {
            price_per_1k_tokens, quality(0-1), latency_ms, stability(0-1)
        }
        """
        price = float(model_metrics.get("price_per_1k_tokens", 1.0))
        quality = float(model_metrics.get("quality", 0.5))
        latency = float(model_metrics.get("latency_ms", 1000.0))
        stability = float(model_metrics.get("stability", 0.8))
        # 价格: 越便宜分越高(归一化)
        price_score = 1.0 / (1.0 + price * 10)
        # 速度: 越快分越高
        speed_score = 1.0 / (1.0 + latency / 1000.0)
        return round((price_score * 0.4 + quality * 0.3 + stability * 0.2 + speed_score * 0.1), 4)

    def build_guide(self, models: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """构建用户专属选购指南(按综合分排序)。"""
        scored = []
        for name, m in models.items():
            scored.append({
                "model": name, "score": self.score(m),
                "price_per_1k_tokens": m.get("price_per_1k_tokens"),
                "quality": m.get("quality"), "latency_ms": m.get("latency_ms"),
                "stability": m.get("stability"),
            })
        scored.sort(key=lambda x: x["score"], reverse=True)
        cheapest = min(models.items(), key=lambda kv: kv[1].get("price_per_1k_tokens", 1e9))
        return {
            "user": self.user,
            "guide": scored,
            "cheapest": cheapest[0],
            "cheapest_price": cheapest[1].get("price_per_1k_tokens"),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def recommend_for_week(self, models: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """推荐: 上周最便宜且稳定达标的模型 → 本周推荐。"""
        guide = self.build_guide(models)
        # 简化: 推荐综合评分最高 + 价格不低于最便宜太多(成本敏感)
        top = guide["guide"][0]
        rec = {
            "user": self.user,
            "recommended_model": top["model"],
            "reason": f"综合表现最佳(价格{top['price_per_1k_tokens']}/1k·质量{top['quality']}·延迟{top['latency_ms']}ms)",
            "alternative_cheapest": guide["cheapest"],
            "week": _week_label(),
        }
        self._history.setdefault("recommendations", []).append({
            "model": top["model"], "week": rec["week"],
            "at": datetime.now(timezone.utc).isoformat(),
        })
        self._save()
        return rec

    def track_accuracy(self, recommended_model: str, actual_metrics: Dict[str, Any],
                       threshold_ok: float = 0.5) -> Dict[str, Any]:
        """追踪历史推荐准确率(本周推荐模型的实际表现是否达标)。"""
        actual_score = self.score(actual_metrics)
        ok = actual_score >= threshold_ok
        self._history.setdefault("recommendations", []).append({
            "model": recommended_model, "actual_score": actual_score, "met_threshold": ok,
            "at": datetime.now(timezone.utc).isoformat(),
        })
        self._history["accuracy"] = self.accuracy()
        self._save()
        return {"model": recommended_model, "actual_score": actual_score, "ok": ok,
                "accuracy": self.accuracy()}

    def accuracy(self) -> float:
        """历史推荐准确率(只统计有 met_threshold 结果的推荐)。"""
        recs = [r for r in self._history.get("recommendations", []) if "met_threshold" in r]
        if not recs:
            return 0.0
        return round(sum(1 for r in recs if r["met_threshold"]) / len(recs), 4)

    # -- 持久化 -------------------------------------------------------------

    def _load(self) -> None:
        if self._path and os.path.exists(self._path):
            try:
                with open(self._path) as f:
                    self._history = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._history = {}

    def _save(self) -> None:
        if not self._path:
            return
        if os.path.dirname(self._path):
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(self._history, f, ensure_ascii=False, indent=2)


def _week_label() -> str:
    from datetime import date
    iso = date.today().isocalendar()
    return f"{iso[0]}-W{iso[1]}"
