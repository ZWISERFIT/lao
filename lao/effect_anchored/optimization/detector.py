# v3.5.1-fix: R4
"""
Anomaly Detector — LAO v3.1 P0-10
===================================

L1 异常检测器: 监测 6 种模型调用异常, 每触发 → 推送第1层(先让人听懂问题·不动手)。

6 种异常(对齐架构文档):
  1. cache_hit_low      : cache_hit < 30% AND cache_calls >= 10
  2. latency_high       : avg_latency > 2000ms AND calls >= 5
  3. error_403_429      : 403/429 errors >= 3 in 24h
  4. token_spike        : token消耗环比 > 120%
  5. duplicate_task     : 同任务类型重复 >= 5次 in 7天
  6. expensive_on_light : heavy_model on light_task >= 10次 in 7天
  7. usage_missing      : usage_missing_count >= 1 (R4)

铁律:
  - 检测异常只是第1层(让人听懂问题), **不动手**, 不直接跳推荐
  - 上周数据作为基线对比(环比)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Anomaly:
    """一次检测到的异常。"""
    type: str                       # 6种类型之一
    severity: str                   # low/mid/high
    detected: bool
    metrics: Dict[str, Any] = field(default_factory=dict)   # 原始技术指标
    suggestion_layer: int = 1       # 永远第1层(先听不懂·不动手)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type, "severity": self.severity,
            "detected": self.detected, "metrics": self.metrics,
            "suggestion_layer": self.suggestion_layer,
        }


class AnomalyDetector:
    """异常检测器(上周数据为基线)。"""

    def __init__(self):
        pass

    def detect(self, window: Dict[str, Any], baseline: Optional[Dict[str, Any]] = None) -> List[Anomaly]:
        """检测 6 种异常。

        Args:
            window: 当前周期(本周)的聚合指标。
            baseline: 上周基线指标(token_spike 环比用)。

        Returns:
            触发的异常列表(只含 detected=True)。
        """
        results = [
            self._cache_hit_low(window),
            self._latency_high(window),
            self._error_403_429(window),
            self._token_spike(window, baseline),
            self._duplicate_task(window),
            self._expensive_on_light(window),
            self._usage_missing(window),
        ]
        return [r for r in results if r.detected]

    # -- 6 种检测 -----------------------------------------------------------

    def _cache_hit_low(self, w: Dict[str, Any]) -> Anomaly:
        cache_hit = float(w.get("cache_hit_pct", 100.0))
        cache_calls = int(w.get("cache_calls", 0))
        detected = cache_hit < 30.0 and cache_calls >= 10
        return Anomaly(
            type="cache_hit_low", severity="low" if detected else "none",
            detected=detected, metrics={"cache_hit_pct": cache_hit, "cache_calls": cache_calls},
        )

    def _latency_high(self, w: Dict[str, Any]) -> Anomaly:
        avg_latency = float(w.get("avg_latency_ms", 0.0))
        calls = int(w.get("total_calls", 0))
        detected = avg_latency > 2000.0 and calls >= 5
        return Anomaly(
            type="latency_high", severity="mid" if detected else "none",
            detected=detected,
            metrics={"avg_latency_ms": avg_latency, "total_calls": calls},
        )

    def _error_403_429(self, w: Dict[str, Any]) -> Anomaly:
        errors = int(w.get("error_403_429", 0))
        detected = errors >= 3
        return Anomaly(
            type="error_403_429", severity="high" if detected else "none",
            detected=detected, metrics={"error_count": errors},
        )

    def _token_spike(self, w: Dict[str, Any], baseline: Optional[Dict[str, Any]]) -> Anomaly:
        cur = float(w.get("total_tokens", 0.0))
        base = float((baseline or {}).get("total_tokens", 0.0))
        if base <= 0:
            return Anomaly(type="token_spike", severity="none", detected=False, metrics={})
        spike_pct = (cur - base) / base * 100.0
        detected = spike_pct > 120.0
        return Anomaly(
            type="token_spike",
            severity="mid" if detected else "none",
            detected=detected,
            metrics={"spike_pct": round(spike_pct, 1), "current_tokens": cur, "baseline_tokens": base},
        )

    def _duplicate_task(self, w: Dict[str, Any]) -> Anomaly:
        dup = int(w.get("duplicate_tasks", 0))
        detected = dup >= 5
        return Anomaly(
            type="duplicate_task", severity="low" if detected else "none",
            detected=detected,
            metrics={"dup_count": dup, "task_type": w.get("top_duplicate_task", "")},
        )

    def _expensive_on_light(self, w: Dict[str, Any]) -> Anomaly:
        affected = int(w.get("expensive_on_light", 0))
        detected = affected >= 10
        return Anomaly(
            type="expensive_on_light", severity="mid" if detected else "none",
            detected=detected, metrics={"affected_count": affected, "heavy_model": w.get("heavy_model", "")},
        )

    def _usage_missing(self, w: Dict[str, Any]) -> Anomaly:
        count = int(w.get("usage_missing_count", 0))
        detected = count >= 1
        return Anomaly(
            type="usage_missing", severity="mid" if detected else "none",
            detected=detected, metrics={"usage_missing_count": count},
        )

    def detect_layer1(self, window: Dict[str, Any], baseline: Optional[Dict[str, Any]] = None) -> List[Anomaly]:
        """检测并确保所有推送都停在第1层(返回带 suggestion_layer=1 的异常)。"""
        return self.detect(window, baseline)
