"""
Data Cleanser — LAO v3.1 P0-5
===============================

L2 数据清洗: ② 授权后扫描本地旧数据(JSONL/日志/配置/Agent记录) → 
提取 pattern → 去噪 → 格式化 Anchor → 本地存储(不上传)。

边界(对齐"数据清洗不是冲洗"):
  - 清洗后的数据仅存本地, 不从设备发出
  - 只做 pattern 提取 + 去噪 + 格式化为 LAO Anchor
  - 不联网、不采集隐私
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


ANCHOR_ANCHOR_TYPES = ("fact", "decision", "cognitive")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(s: str) -> str:
    return (s or "").strip().lower()


class DataCleanser:
    """本地旧数据 → LAO Anchor 的清洗器。

    用法:
      dc = DataCleanser()
      anchors = dc.cleanse({
          "patterns": ["退款>500超阈值需人工", "高峰期优先缓存命中"],
          "decisions": [{"condition": "...", "action": "..."}],
          "joins": [],   # 可传 jsonl 行
      })
      # anchors 是格式化好的 Anchor dict, 交给 CognitiveAnchorStore 本地写入
    """

    def __init__(self, dirty_threshold: int = 3):
        self.dirty_threshold = dirty_threshold  # 去噪: 太短的 pattern 忽略

    def cleanse(self, raw: Dict[str, Any]) -> List[Dict[str, Any]]:
        """从原始数据清洗出 LAO Anchor(本地)。

        Args:
            raw: 原始数据, 含任意键. 常见:
              - "patterns": 提取的 pattern 列表(字符串或 dict)
              - "decisions": 决策规则列表
              - "logs"/"records": 日志/记录行(尝试提取)

        Returns:
            格式化好的 Anchor dict 列表(写入 CognitiveAnchorStore)。
        """
        anchors: List[Dict[str, Any]] = []

        # 1) 错误/决策 pattern → Decision/Cognitive Anchor
        for pat in raw.get("patterns", []) or []:
            a = self._cleanse_pattern(pat)
            if a:
                anchors.append(a)

        # 2) 显式决策规则 → Decision Anchor
        for dec in raw.get("decisions", []) or []:
            a = self._cleanse_decision(dec)
            if a:
                anchors.append(a)

        # 3) 日志/记录行 → 尝试提取事实/决策
        for rec in (raw.get("logs") or raw.get("records") or []):
            a = self._cleanse_record(rec)
            if a:
                anchors.append(a)

        return anchors

    # -- 各类型清洗 ---------------------------------------------------------

    def _cleanse_pattern(self, pat: Any) -> Optional[Dict[str, Any]]:
        """去噪 + 格式化一条 pattern。太短/空视为噪声丢弃。"""
        if isinstance(pat, dict):
            rule = str(pat.get("rule") or pat.get("pattern") or pat.get("content") or "")
            anchor_type = str(pat.get("anchor_type") or "decision")
        else:
            rule = str(pat or "").strip()
            anchor_type = "decision"
        rule = rule.strip()
        if len(rule) < self.dirty_threshold:
            return None  # 噪声
        if anchor_type not in ANCHOR_ANCHOR_TYPES:
            anchor_type = "decision"
        return {
            "anchor_type": anchor_type,
            "value": {"rule": rule, "source": "data-cleanser:pattern"},
            "tags": ["cleansed", anchor_type],
            "trust_weight": 0.7,
            "created_at": _now_iso(),
        }

    def _cleanse_decision(self, dec: Any) -> Optional[Dict[str, Any]]:
        if isinstance(dec, dict):
            cond = str(dec.get("condition") or dec.get("trigger") or "")
            action = str(dec.get("action") or dec.get("decision") or "")
            rule = f"当{cond}时→{action}" if cond and action else (cond or action)
        else:
            rule = str(dec or "").strip()
        rule = rule.strip()
        if len(rule) < self.dirty_threshold:
            return None
        return {
            "anchor_type": "decision",
            "value": {"rule": rule, "source": "data-cleanser:decision"},
            "tags": ["cleansed", "decision"],
            "trust_weight": 0.75,
            "created_at": _now_iso(),
        }

    def _cleanse_record(self, rec: Any) -> Optional[Dict[str, Any]]:
        """从一条记录行尝试提取事实/决策。"""
        try:
            if isinstance(rec, str):
                line = rec.strip()
                if not line or len(line) < self.dirty_threshold:
                    return None
                return {
                    "anchor_type": "fact",
                    "value": {"fact": line, "source": "data-cleanser:record"},
                    "tags": ["cleansed", "fact"],
                    "trust_weight": 0.5,
                    "created_at": _now_iso(),
                }
            if isinstance(rec, dict):
                return self._cleanse_decision(rec)
        except (TypeError, ValueError):
            return None
        return None

    # -- 汇总统计 -----------------------------------------------------------

    def summary(self, raw: Dict[str, Any], anchors: List[Dict[str, Any]]) -> Dict[str, Any]:
        """清洗摘要(用于授权窗口展示·本地)。"""
        by_type: Dict[str, int] = {}
        for a in anchors:
            by_type[a["anchor_type"]] = by_type.get(a["anchor_type"], 0) + 1
        return {
            "scanned_sources": list(raw.keys()),
            "raw_items": sum(len(raw.get(k, []) or []) for k in raw),
            "produced_anchors": len(anchors),
            "by_type": by_type,
            "location": "local-only",
            "note": "清洗后的数据仅存本地(不从设备发出)",
        }
