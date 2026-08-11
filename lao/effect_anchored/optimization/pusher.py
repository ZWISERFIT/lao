"""
Suggestion Pusher — LAO v3.1 P1-13
====================================

建议推送器: 7天周报机制 + 三步递进法。

三步递进铁律(架构文档):
  0. 检测异常(detector)
  1. 翻译成人话并停在此层(translator) — 让用户先听懂问题·**不动手**
  2. 用户点「了解详情」→ 才给具体方案
  3. 最后才推合作伙伴/平台

🚫 绝不从异常检测直接跳到推平台 = 推销。

WeekSummary: 总调用 / 缓存节省 / 延迟 / 新发现。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class WeekSummary:
    """7 天周报。"""
    total_calls: int = 0
    cache_savings_tokens: int = 0
    avg_latency_ms: float = 0.0
    new_insights: int = 0            # 新发现的经验/锚点
    anomalies_detected: int = 0
    week: str = ""
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "week": self.week, "total_calls": self.total_calls,
            "cache_savings_tokens": self.cache_savings_tokens,
            "avg_latency_ms": self.avg_latency_ms,
            "new_insights": self.new_insights,
            "anomalies_detected": self.anomalies_detected,
            "generated_at": self.generated_at,
        }


class SuggestionPusher:
    """三步递进推送器。"""

    # 用户当前处于的递进层(0检测 / 1人话生成 / 2用户了解详情 / 3推方案合作)
    def __init__(self):
        self._layer_state: Dict[str, int] = {}   # anomaly_id -> layer

    def step1_present(self, translated: Dict[str, Any],
                      anomaly_id: str = "") -> Dict[str, Any]:
        """第1层: 只把已翻译的人话给用户看·**不动手**·不给方案。"""
        self._layer_state[anomaly_id or translated.get("type", "?")] = 1
        return {
            "layer": 1,
            "title": translated.get("title", ""),
            "body": translated.get("body", ""),
            "cta_ready": False,          # 不直接给方案
            "has_detail": True,          # 提供"了解详情"入口
            "detail_action": "了解详情",
        }

    def step2_detail(self, translated: Dict[str, Any],
                     anomaly_id: str = "") -> Dict[str, Any]:
        """第2层: 用户点「了解详情」→ 才给具体方案。"""
        key = anomaly_id or translated.get("type", "?")
        # 必须从第1层进入, 不允许跳过
        if self._layer_state.get(key, 0) < 1:
            return {"layer": 1, "error": "must first present at layer 1",
                    "reask": translated.get("title", "")}
        self._layer_state[key] = 2
        return {
            "layer": 2,
            "detail": translated.get("body", ""),
            "recommended_action": translated.get("cta", ""),
            "engage_partner": False,      # 还没到最后推合作伙伴
        }

    def step3_partner(self, translated: Dict[str, Any],
                      partner_id: str, anomaly_id: str = "") -> Dict[str, Any]:
        """第3层: 用户接受了改进方案 → 最后才推合作伙伴(不硬推)。"""
        key = anomaly_id or translated.get("type", "?")
        if self._layer_state.get(key, 0) < 2:
            return {"layer": 2, "error": "must pass layer 2 before partner",
                    "reask": translated.get("cta", "")}
        self._layer_state[key] = 3
        return {
            "layer": 3,
            "optin_partner": partner_id,   # 需用户主动选择
            "is_promotion": False,         # 非推销(用户已到接受方案阶段)
            "never_force": True,           # 绝不强制
        }

    def weekly_summary(self, summary: WeekSummary) -> Dict[str, Any]:
        """生成 7 天周报(人话概括)。"""
        return {
            "kind": "weekly_summary",
            "title": f"你 Agent 本周运行报告",
            "body": (
                f"共 {summary.total_calls} 次调用, 缓存节省约 {summary.cache_savings_tokens} token。"
                f"平均延迟 {summary.avg_latency_ms}ms。"
                f"新发现 {summary.new_insights} 条经验, 检测到 {summary.anomalies_detected} 个可优化点。"
            ),
            "data": summary.to_dict(),
        }

    def prompt_weekly(self, owner: str) -> str:
        """触发周报的问询(每周一次)。"""
        return f"{owner}, 想看这周的运行报告吗? (总调用/缓存节省/延迟/新发现)"
