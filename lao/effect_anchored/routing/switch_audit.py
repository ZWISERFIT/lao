"""
Switch Audit — LAO v3.1 P1-14
===============================

路由切换审计日志: 记录每次模型/provider 切换的完整轨迹。

@dataclass SwitchAuditEntry:
  timestamp / task_id / task_type / from_provider / from_model /
  to_provider / to_model / reason / triggered_by

SwitchAuditor:
  record()   : 每次切换自动记录
  recent(24h): 24h 可追溯
  audit_trail : 按日期范围导出(每周审计报告)
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SwitchAuditEntry:
    """一次路由切换的审计记录。"""
    timestamp: str = field(default_factory=_now_iso)   # ISO UTC
    task_id: str = ""
    task_type: str = ""                     # code_generation / translation / heartbeat ...
    from_provider: str = ""
    from_model: str = ""
    to_provider: str = ""
    to_model: str = ""
    reason: str = ""                        # 切换原因(超时/403/成本/性能)
    triggered_by: str = "auto"              # auto / user / consent

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SwitchAuditor:
    """切换审计器(持久化到本地 JSONL)。"""

    def __init__(self, log_path: Optional[str] = None):
        self.log_path = log_path or os.path.join(
            os.path.dirname(__file__), "..", "..", "switch_audit.jsonl"
        )

    def record(self, entry: SwitchAuditEntry) -> Dict[str, Any]:
        """记录一次切换。"""
        d = entry.to_dict()
        if not d.get("timestamp"):
            d["timestamp"] = _now_iso()
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        with open(self.log_path, "a") as f:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
        return d

    def _read_all(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.log_path):
            return []
        entries = []
        with open(self.log_path) as f:
            for line in f:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return entries

    def recent(self, hours: int = 24) -> List[Dict[str, Any]]:
        """最近 N 小时的可追溯切换记录。"""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        out = []
        for d in self._read_all():
            try:
                ts = datetime.fromisoformat(d["timestamp"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= cutoff:
                    out.append(d)
            except (KeyError, ValueError, TypeError):
                continue
        return out

    def audit_trail(self, start: str, end: Optional[str] = None) -> List[Dict[str, Any]]:
        """按日期范围(ISO)导出, 供每周审计报告。"""
        end = end or _now_iso()
        out = []
        for d in self._read_all():
            try:
                ts = d["timestamp"]
                if start <= ts <= end:
                    out.append(d)
            except KeyError:
                continue
        return out

    def weekly_report(self) -> Dict[str, Any]:
        """每周审计报告摘要。"""
        entries = self.audit_trail(
            (datetime.now(timezone.utc) - timedelta(days=7)).isoformat())
        by_task: Dict[str, int] = {}
        switch_reasons: Dict[str, int] = {}
        for e in entries:
            by_task[e.get("task_type", "unknown")] = by_task.get(e.get("task_type", "unknown"), 0) + 1
            switch_reasons[e.get("reason", "unknown")] = switch_reasons.get(e.get("reason", "unknown"), 0) + 1
        return {
            "period": "last_7d",
            "total_switches": len(entries),
            "by_task_type": by_task,
            "by_reason": switch_reasons,
            "entries": entries,
        }
