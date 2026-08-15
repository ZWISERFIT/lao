"""
RIS → LAO 数据桥 (P0-3 · 成熟部署加速 · Shuyu 立项)
=====================================================================================
问题: RIS 已收集 1910+ 事件，但全部躺在 ris-events.jsonl 日志里，未进入 LAO/Stella
      决策闭环。RIS 是"身体的神经末梢"，LAO 是"大脑"，桥就是"神经纤维"。

目标: 让 RIS 检测到的运行健康事件结构化落盘到共享 JSON，供:
      - LAO 路由决策(routing)读取 → 成本/可用性信号进入 provider 选择
      - Stella 审计读取 → 一键看到今天跑了几次恢复、成本链路断没断
      - 智囊团复盘读取 → 不再逐个 grep jsonl

设计约束(与既有架构对齐):
  - 不重写 LAO，不新增架构(方案 B 软分层)
  - 复用 RuntimeHealthEvent 序列化(to_dict)
  - 落盘位置遵循共享状态约定: /home/agentuser/shared/state/

输出 JSON 结构(ris-bridge.json):
  {
    "layer": "ris",
    "schema_version": "1.0",
    "generated_at": "ISO-8601",
    "window": { "since": "ISO", "events_total": N },
    "summary": {
      "events_by_type": { "cpu_anomaly": N, ... },
      "recoveries": { "cpu_recovery": N, ... },
      "provider_status": { "lao-router": "healthy|down", ... },
      "latest_cpu_pct": 42.3,
      "latest_mem_pct": 58.1
    },
    "recent_events": [ <RuntimeHealthEvent.to_dict()> ... ],
    "active_alerts": [ <未恢复的 critical/error 事件> ... ]
  }
"""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ris.events import RuntimeHealthEvent

# ── 共享 JSON 落盘位置(共享状态约定) ────────────────────────────────
BRIDGE_DIR = "/home/agentuser/shared/state"
BRIDGE_FILE = os.path.join(BRIDGE_DIR, "ris-bridge.json")

# 事件源(agent.py 的 append-only jsonl)
_RIS_LOG = "/home/agentuser/.openclaw/workspace/tristan/tech_lead/logs/ris-events.jsonl"

# 保留最近 N 条进入 bridge(避免 JSON 无限膨胀)
MAX_RECENT_EVENTS = 50

# 桥运行元信息
SCHEMA_VERSION = "1.0"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RISToLAOBridge:
    """RIS → LAO 数据桥: 把 ris-events.jsonl 聚合为共享 JSON 供 LAO/Stella 消费。"""

    def __init__(self, bridge_file: str = BRIDGE_FILE, source_log: str = _RIS_LOG,
                 max_recent: int = MAX_RECENT_EVENTS):
        self.bridge_file = bridge_file
        self.source_log = source_log
        self.max_recent = max_recent

    # ── 读取事件源 ────────────────────────────────────────
    def load_events(self, limit: Optional[int] = None) -> List[Dict]:
        """从 ris-events.jsonl 读取事件(追加式·只读)。"""
        events: List[Dict] = []
        try:
            with open(self.source_log, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if limit:
                lines = lines[-limit:]
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except FileNotFoundError:
            return []
        return events

    # ── 聚合 summary ──────────────────────────────────────
    def aggregate(self, events: List[Dict]) -> Dict:
        """把事件列表聚合为 summary + active_alerts(供 sync/build 复用)。"""
        by_type: Counter = Counter()
        recoveries: Counter = Counter()
        provider_status: Dict[str, str] = {}
        latest_cpu = latest_mem = 0.0
        active_alerts: List[Dict] = []

        for e in events:
            et = e.get("event_type", "unknown")
            by_type[et] += 1

            # 恢复类事件(五步闭环 Record 产物)
            if et.endswith("_recovery") or e.get("status") in ("recovered", "recovering"):
                recoveries[et] += 1

            # provider 状态(provider_unavailable / provider_ok)
            if et in ("provider_unavailable", "provider_ok"):
                pid = e.get("agent_id", e.get("detail", {}).get("provider", "?"))
                provider_status[pid] = "healthy" if et == "provider_ok" else "down"

            # 最新 CPU/Memory
            d = e.get("detail", {})
            if et == "cpu_anomaly" and "cpu_pct" in d:
                latest_cpu = d["cpu_pct"]
            if et == "memory_anomaly" and "mem_pct" in d:
                latest_mem = d["mem_pct"]

            # active alerts: 未恢复的 critical/error(非 recovered/recovering)
            if e.get("severity") in ("critical", "error") and \
               e.get("status") not in ("recovered", "recovering"):
                active_alerts.append(e)

        summary = {
            "events_by_type": dict(by_type),
            "recoveries": dict(recoveries),
            "provider_status": provider_status,
            "latest_cpu_pct": latest_cpu,
            "latest_mem_pct": latest_mem,
        }
        return {"summary": summary, "active_alerts": active_alerts[-10:]}

    # ── 构建桥 JSON(不落盘·供测试/预览) ─────────────────────
    def build(self, events: Optional[List[Dict]] = None) -> Dict:
        events = events if events is not None else self.load_events()
        agg = self.aggregate(events)
        return {
            "layer": "ris",
            "schema_version": SCHEMA_VERSION,
            "generated_at": _now(),
            "window": {
                "since": events[0]["ts"] if events else "",
                "events_total": len(events),
            },
            "summary": agg["summary"],
            "recent_events": events[-self.max_recent:],
            "active_alerts": agg["active_alerts"],
        }

    # ── 落盘(原子写) ───────────────────────────────────────
    def sync(self, events: Optional[List[Dict]] = None) -> Dict:
        bridge = self.build(events)

        os.makedirs(os.path.dirname(self.bridge_file), exist_ok=True)
        tmp = self.bridge_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(bridge, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.bridge_file)
        return bridge


def sync_bridge() -> Dict:
    """便捷入口: 立即同步一次 RIS → 共享 JSON。"""
    return RISToLAOBridge().sync()


__all__ = ["RISToLAOBridge", "sync_bridge", "BRIDGE_FILE",
           "BRIDGE_DIR", "SCHEMA_VERSION"]
