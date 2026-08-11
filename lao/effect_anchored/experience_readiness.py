"""
Experience Readiness Tracker — LAO v3.1 P0-1
==============================================

经验量追踪器：本地计算经验是否「量达标」，达标则推送第③授权(经验上传评估)。

四条件(本地计算, 默认阈值可配置):
  1. trigger_count : 经验被触发次数 ≥ min_trigger (默认 5)
  2. cross_domain   : 跨领域使用数 ≥ min_cross_domain (默认 1)
  3. age_days       : 经验年龄(天) ≥ min_age_days (默认 7)
  4. confidence     : 经验置信度 ≥ min_confidence (默认 0.7)

规则: 四条件**同时满足** → ready → 推送第③授权。

边界:
  - 全本地计算(不联网·不采集隐私)
  - 只做"量是否达标"判定, 不实现授权弹窗(那是 Consent Gate 的活)
  - 推送③的决策由调用方(Consent Gate / UI)根据 ready 结果执行
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class ReadinessConfig:
    """四条件阈值(可配置)。"""
    min_trigger: int = 5          # trigger_count 阈值
    min_cross_domain: int = 1     # cross_domain 阈值
    min_age_days: int = 7         # age_days 阈值
    min_confidence: float = 0.7   # confidence 阈值


@dataclass
class ReadinessResult:
    """单条经验的量达标评估结果。"""
    experience_id: str
    ready: bool                    # 四条件是否同时满足
    conditions: Dict[str, bool]    # {trigger_count: bool, cross_domain: bool, ...}
    met: List[str]                 # 满足的条件名
    unmet: List[str]               # 未满足的条件名
    suggestion: str                # 人类可读建议

    def to_dict(self) -> Dict[str, Any]:
        return {
            "experience_id": self.experience_id,
            "ready": self.ready,
            "conditions": self.conditions,
            "met": self.met,
            "unmet": self.unmet,
            "suggestion": self.suggestion,
        }


def _days_since(ts_str: Optional[str]) -> float:
    """计算 ISO 时间戳距今的天数；无则返回 0。"""
    if not ts_str:
        return 0.0
    try:
        ts = datetime.fromisoformat(ts_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0
    except (ValueError, TypeError):
        return 0.0


class ExperienceReadinessTracker:
    """经验量追踪器: 评估经验是否量达标(四条件)。

    用法:
      tracker = ExperienceReadinessTracker()            # 默认阈值
      res = tracker.check_experience({
          "id": "exp-1", "trigger_count": 15,
          "cross_domain": 2, "created_at": "2026-08-01T00:00:00+08:00",
          "confidence": 0.85,
      })   # -> ReadinessResult.ready=True  → 推动第③授权
    """

    def __init__(self, config: Optional[ReadinessConfig] = None):
        self.config = config or ReadinessConfig()

    def check_experience(self, meta: Dict[str, Any]) -> ReadinessResult:
        """评估单条经验是否达标。meta 需含 id/trigger_count/cross_domain/created_at/confidence。"""
        exp_id = str(meta.get("id") or meta.get("experience_id") or "unknown")
        trigger_count = int(meta.get("trigger_count", 0) or 0)
        cross_domain = int(meta.get("cross_domain", 0) or 0)
        confidence = float(meta.get("confidence", 0.0) or 0.0)
        age_days = float(meta.get("age_days", 0)) or _days_since(meta.get("created_at"))

        cond = {
            "trigger_count": trigger_count >= self.config.min_trigger,
            "cross_domain": cross_domain >= self.config.min_cross_domain,
            "age_days": age_days >= self.config.min_age_days,
            "confidence": confidence >= self.config.min_confidence,
        }
        met = [c for c, ok in cond.items() if ok]
        unmet = [c for c, ok in cond.items() if not ok]
        ready = bool(cond["trigger_count"] and cond["cross_domain"]
                     and cond["age_days"] and cond["confidence"])

        if ready:
            suggestion = f"经验[{exp_id}] 量已达标({trigger_count}次触发·{cross_domain}域·{age_days:.0f}天·信任{confidence}) — 推送第③授权(经验上传评估)"
        else:
            suggestion = f"经验[{exp_id}] 未达标: 缺 {', '.join(unmet) or '条件'} (触发{trigger_count}·域{cross_domain}·{age_days:.0f}天·信任{confidence})"

        return ReadinessResult(
            experience_id=exp_id, ready=ready, conditions=cond,
            met=met, unmet=unmet, suggestion=suggestion,
        )

    def evaluate_batch(self, experiences: List[Dict[str, Any]]) -> List[ReadinessResult]:
        """批量评估, 返回全部结果(调用方据此挑 ready 的推送③)。"""
        return [self.check_experience(e) for e in experiences]

    def ready_batch(self, experiences: List[Dict[str, Any]]) -> List[ReadinessResult]:
        """只返回 ready 的经验(量达标→推送③授权)。"""
        return [r for r in self.evaluate_batch(experiences) if r.ready]
