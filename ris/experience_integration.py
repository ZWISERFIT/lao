"""RIS→LAO 数据桥 (P0-3·Shuyu立项·RIS 加速成熟部署)

把 RIS 事件从日志(ris-events.jsonl)聚合为共享结构化数据,
供 LAO/Stella 决策消费·形成闭环(不再只躺在日志)。

输出:
- ris/experience/data/ris_summary.json: 按事件类型/严重度聚合摘要
- 即时读(每次生成替换)·Stella/LAO 可拉取

RIS 是 LAO 三分离后的风险经验库·数据桥让风险事件进入决策链。
"""
from __future__ import annotations
import json, os
from collections import defaultdict
from datetime import datetime

RIS_EVENT_LOG = "/home/agentuser/.openclaw/workspace/tristan/tech_lead/logs/ris-events.jsonl"
OUT_DIR = "/home/agentuser/lao-release/ris/experience/data"
os.makedirs(OUT_DIR, exist_ok=True)


def load_events(path: str = RIS_EVENT_LOG) -> list:
    events = []
    if not os.path.exists(path):
        return events
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except Exception:
                continue
    return events


def build_summary(events: list) -> dict:
    """聚合 RIS 事件为决策摘要(按类型/严重度/对象/时段)。"""
    by_type = defaultdict(int)
    by_severity = defaultdict(int)
    by_agent = defaultdict(int)
    for e in events:
        by_type[e.get("event_type", "unknown")] += 1
        by_severity[e.get("severity", "info")] += 1
        by_agent[e.get("agent_id", "unknown")] += 1

    # 活跃风险(最近事件·detected 状态)
    active_risks = []
    for e in events[-20:]:
        if e.get("status") == "detected":
            active_risks.append({
                "event_type": e.get("event_type"),
                "agent_id": e.get("agent_id"),
                "severity": e.get("severity"),
                "detail": e.get("detail", {}),
                "ts": e.get("ts"),
            })

    return {
        "source": "ris-events.jsonl",
        "total_events": len(events),
        "generated_at": datetime.now().isoformat(),
        "by_event_type": dict(by_type),
        "by_severity": dict(by_severity),
        "by_agent": dict(by_agent),
        "active_risks": active_risks,
        "recommendation": _recommend(by_type, by_severity),
    }


def _recommend(by_type: dict, by_severity: dict) -> str:
    """基于风险分布给 LAO/Stella 建议。"""
    if by_type.get("session_bloat", 0) > 100:
        return "session_bloat 高频: 建议清理 >8MB 旧 session(webchat 性能根因·用户可感知)"
    if by_type.get("cpu_anomaly", 0) > 300:
        return "cpu_anomaly 高频: 建议排查高负载进程·考虑加内存或降级"
    if by_type.get("provider_unavailable", 0) > 0:
        return "provider_unavailable: 有 provider 掉线·检查 lao-router/直连稳定性"
    return "风险水平正常"


def dump_summary():
    events = load_events()
    summary = build_summary(events)
    out_path = os.path.join(OUT_DIR, "ris_summary.json")
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1, default=str)
    print(f"✅ RIS→LAO 数据桥: {len(events)} 事件 → {out_path}")
    print(f"   by_type: {dict(summary['by_event_type'])}")
    print(f"   by_severity: {dict(summary['by_severity'])}")
    print(f"   active_risks: {len(summary['active_risks'])} 条")
    print(f"   recommendation: {summary['recommendation']}")
    return out_path


if __name__ == "__main__":
    dump_summary()
