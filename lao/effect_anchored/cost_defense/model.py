# P0 LAO成本防线: 规格书 _rev2 (SHA-256 4d6bc3aa…17c4) 施工件
"""LAO 成本防线 — 公共数据模型与时间口径。

规格锚点 (77号 _rev2):
    - S1-6 时间口径: 归窗一律 UTC, 日窗 00:00~24:00 UTC, `ts` 标准化为 UTC ISO-8601。
    - S1-5 告警字段: 含 agent_cost_breakdown / escalation_level / escalated_to。
    - R-02: 合成测试记录 (task="test") 封存, 不进入回归统计与成本口径。

约束: 仅标准库 · 只读旁路(不写任何真实账本) · fail-open(解析异常跳过该条不抛)。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

UTC = timezone.utc

# R-02: 合成/测试数据标记 — 命中即封存, 不计入成本口径
SYNTHETIC_TASK_MARKERS = frozenset({"test", "testcase", "synthetic"})

# S1-2: 成本口径来源标注
COST_BASIS_OFFICIAL = "official_ledger"
COST_BASIS_LOCAL = "local_estimate"
COST_BASIS_UNRECONCILED = "unreconciled"

# agent 维度缺失时的显式占位 — 禁止用猜测值填充(N-01 口径纪律)
AGENT_UNATTRIBUTED = "unattributed"


def parse_ts(raw: Any) -> Optional[datetime]:
    """解析时间戳为 UTC aware datetime; 无时区者按 UTC 处理(S1-6)。

    Args:
        raw: ISO-8601 字符串。

    Returns:
        UTC aware datetime; 无法解析返回 None(fail-open)。
    """
    if not isinstance(raw, str) or not raw:
        return None
    text = raw.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def iso_utc(dt: datetime) -> str:
    """输出 UTC ISO-8601 字符串(S1-6: 不做本地时区换算)。"""
    return dt.astimezone(UTC).isoformat()


def day_key(dt: datetime) -> str:
    """UTC 日窗键 YYYY-MM-DD。"""
    return dt.astimezone(UTC).strftime("%Y-%m-%d")


def hour_key(dt: datetime) -> str:
    """UTC 小时窗键 YYYY-MM-DDTHH(整点对齐)。"""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H")


def is_synthetic(task: Any) -> bool:
    """判定是否为合成/测试记录(R-02 封存口径)。"""
    return isinstance(task, str) and task.strip().lower() in SYNTHETIC_TASK_MARKERS


@dataclass
class CostEvent:
    """路由成本事件(来源: routing_cost_log.json, 只读)。"""

    ts: Optional[datetime]
    provider: str = ""
    model: str = ""
    task: str = ""
    task_id: str = ""
    task_type: str = ""
    agent: str = AGENT_UNATTRIBUTED
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    synthetic: bool = False
    source: str = ""

    def task_id_key(self) -> str:
        """单任务聚合键: 仅用显式 task_id。

        `task` 字段在 routing_cost_log 里是任务**类型**(test/medium/code),
        用它当任务键会把整类费用归成一笔而虚报, 故不参与归因。
        返回空串 = 本笔不可归因到单任务(由调用方计入归因缺口)。
        """
        return (self.task_id or "").strip()

    @classmethod
    def from_routing_record(cls, rec: Dict[str, Any], source: str = "") -> Optional["CostEvent"]:
        """从 routing_cost_log.json 的单条 record 构造(字段缺失容错)。"""
        if not isinstance(rec, dict):
            return None
        task = str(rec.get("task", "") or "")
        agent = str(rec.get("agent", "") or "").strip() or AGENT_UNATTRIBUTED
        try:
            cost = float(rec.get("cost_usd", 0.0) or 0.0)
        except (TypeError, ValueError):
            cost = 0.0
        return cls(
            ts=parse_ts(rec.get("timestamp")),
            provider=str(rec.get("provider", "") or ""),
            model=str(rec.get("model", "") or ""),
            task=task,
            task_id=str(rec.get("task_id", "") or ""),
            task_type=str(rec.get("task_type", "") or ""),
            agent=agent,
            cost_usd=cost,
            tokens_in=int(rec.get("tokens_in", 0) or 0),
            tokens_out=int(rec.get("tokens_out", 0) or 0),
            synthetic=is_synthetic(task),
            source=source,
        )


@dataclass
class SwitchEvent:
    """provider/model 切换审计事件(来源: switch_audit.jsonl, 只读)。"""

    ts: Optional[datetime]
    from_provider: str = ""
    from_model: str = ""
    to_provider: str = ""
    to_model: str = ""
    reason: str = ""
    task_id: str = ""
    task_type: str = ""
    triggered_by: str = ""
    source: str = ""

    @classmethod
    def from_audit_record(cls, rec: Dict[str, Any], source: str = "") -> Optional["SwitchEvent"]:
        """从 switch_audit.jsonl 的单行构造(字段缺失容错)。"""
        if not isinstance(rec, dict):
            return None
        return cls(
            ts=parse_ts(rec.get("timestamp")),
            from_provider=str(rec.get("from_provider", "") or ""),
            from_model=str(rec.get("from_model", "") or ""),
            to_provider=str(rec.get("to_provider", "") or ""),
            to_model=str(rec.get("to_model", "") or ""),
            reason=str(rec.get("reason", "") or ""),
            task_id=str(rec.get("task_id", "") or ""),
            task_type=str(rec.get("task_type", "") or ""),
            triggered_by=str(rec.get("triggered_by", "") or ""),
            source=source,
        )


def load_cost_events(path: str) -> List[CostEvent]:
    """只读加载路由成本事件; 文件缺失或损坏返回空列表(fail-open)。"""
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return []
    records = payload.get("records") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        return []
    events: List[CostEvent] = []
    for rec in records:
        event = CostEvent.from_routing_record(rec, source=path)
        if event is not None:
            events.append(event)
    return events


def load_switch_events(
    path: str,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    reason: Optional[str] = None,
) -> List[SwitchEvent]:
    """只读加载切换审计事件, 支持 UTC 时间窗与 reason 过滤。

    Args:
        path: switch_audit.jsonl 路径(只读)。
        since/until: UTC 时间窗 [since, until)。
        reason: 精确匹配的 reason 值(如 quota_degrade_flash)。

    Returns:
        SwitchEvent 列表; 单行解析失败跳过该行(fail-open)。
    """
    if not os.path.isfile(path):
        return []
    events: List[SwitchEvent] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                event = SwitchEvent.from_audit_record(rec, source=path)
                if event is None or event.ts is None:
                    continue
                if since is not None and event.ts < since:
                    continue
                if until is not None and event.ts >= until:
                    continue
                if reason is not None and event.reason != reason:
                    continue
                events.append(event)
    except OSError:
        return events
    return events


@dataclass
class Alert:
    """告警记录(S1-5 字段集, 同时作为 S3 双路由消息体)。"""

    alert_id: str
    ts: str
    level: str
    axis: str
    trigger_rule: str
    window: str
    dedup_key: str
    provider: str = ""
    model: str = ""
    task_id: str = ""
    agent_cost_breakdown: Dict[str, float] = field(default_factory=dict)
    sample_details: List[Dict[str, Any]] = field(default_factory=list)
    cost_usd: float = 0.0
    cost_basis: str = COST_BASIS_UNRECONCILED
    budget_usd: float = 0.0
    status: str = "fired"
    escalation_level: Optional[str] = None
    escalated_to: Optional[str] = None
    delivered_to: List[str] = field(default_factory=list)

    def to_record(self) -> Dict[str, Any]:
        """序列化为落盘/出箱记录(字段顺序与 S1-5 规格一致)。"""
        return {
            "alert_id": self.alert_id,
            "ts": self.ts,
            "level": self.level,
            "axis": self.axis,
            "trigger_rule": self.trigger_rule,
            "window": self.window,
            "provider": self.provider,
            "model": self.model,
            "task_id": self.task_id,
            "agent_cost_breakdown": self.agent_cost_breakdown,
            "sample_details": self.sample_details,
            "cost_usd": round(self.cost_usd, 6),
            "cost_basis": self.cost_basis,
            "budget_usd": self.budget_usd,
            "dedup_key": self.dedup_key,
            "status": self.status,
            "escalation_level": self.escalation_level,
            "escalated_to": self.escalated_to,
            "delivered_to": self.delivered_to,
        }
