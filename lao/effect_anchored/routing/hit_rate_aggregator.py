"""命中率聚合脚本 — 小时/任务级命中率报表数据源(Stella 派单·Nova 报表用)

从 lao-router-events.jsonl 聚合:
- 按 agent × task_type × 时段(小时) 的命中率明细
- 输出 JSON + 简表(可被 Nova 直接消费)

命中率 = cache_hit_tokens / (cache_hit_tokens + cache_miss_tokens)
"""
from __future__ import annotations
import json, os, sys
from collections import defaultdict
from datetime import datetime

EVENT_LOG = "/home/agentuser/.openclaw/workspace/tristan/tech_lead/logs/lao-router-events.jsonl"
OUT_DIR = "/home/agentuser/zwiserfit-os/finance/data"
os.makedirs(OUT_DIR, exist_ok=True)


def load_events(path: str = EVENT_LOG) -> list:
    events = []
    if not os.path.exists(path):
        return events
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except Exception:
                continue
    return events


def hour_of(ts: str) -> str:
    """从 ISO ts 提取小时(如 2026-08-15T14 → '2026-08-15 14:00')。"""
    try:
        # ts 格式: 2026-08-15T14:42:30+0800
        d = datetime.fromisoformat(ts.replace("+0800", "+08:00").replace("+0000", "+00:00"))
        return d.strftime("%Y-%m-%d %H:00")
    except Exception:
        return ts[:13] + ":00"


def aggregate(events: list, min_cache_tokens: int = 200) -> dict:
    """按 agent × task_type × 小时 聚合命中率。

    P1 监测口径修正(Shuyu派单 2026-08-15):
    - min_cache_tokens=200: 短请求(cache_hit+miss < 200 tokens·无缓存价值)不算分母
    - 避免短请求稀释命中率指标(短请求基本无缓存前缀·非真实 miss)
    """
    # (agent, task_type, hour) → {hit, miss, count, short_count}
    agg = defaultdict(lambda: {"hit": 0, "miss": 0, "count": 0, "short": 0})
    for e in events:
        agent = e.get("agent") or "unknown"
        task = e.get("task_type") or e.get("tier") or "unknown"
        hour = hour_of(e.get("ts", ""))
        hit = e.get("cache_hit_tokens") or 0
        miss = e.get("cache_miss_tokens") or 0
        key = (agent, task, hour)
        # 短请求(无缓存价值)不计入命中率分母·单独统计
        if hit + miss < min_cache_tokens:
            agg[key]["short"] += 1
            continue
        agg[key]["hit"] += hit
        agg[key]["miss"] += miss
        agg[key]["count"] += 1

    # 转可读结构
    rows = []
    for (agent, task, hour), v in agg.items():
        total = v["hit"] + v["miss"]
        rate = round(v["hit"] / total * 100, 2) if total > 0 else None
        rows.append({
            "agent": agent, "task_type": task, "hour": hour,
            "cache_hit_tokens": v["hit"], "cache_miss_tokens": v["miss"],
            "hit_rate_pct": rate, "requests": v["count"],
            "short_requests_skipped": v["short"],
        })
    rows.sort(key=lambda r: r["hour"])
    return rows


def main():
    events = load_events()
    rows = aggregate(events)

    out_json = os.path.join(OUT_DIR, "hit-rate-by-agent-task-hour.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"generated_at": datetime.now().isoformat(),
                   "WARNING_FROZEN_20260817": "命中率已冻结对外发布·router局部视图(覆盖~22%流量)≠全局命中率·全局以官方DeepSeek CSV为唯一ground truth·见hit-rate-divergence-rootcause-20260817.md",
                   "total_events": len(events), "rows": rows},
                  f, ensure_ascii=False, indent=1)
    print(f"✅ 聚合完成: {len(events)} 事件 → {len(rows)} 行 (agent×task×hour)")
    print(f"输出: {out_json}")
    print()
    # 简表: 按 agent 汇总
    by_agent = defaultdict(lambda: {"hit": 0, "miss": 0, "count": 0})
    for r in rows:
        a = r["agent"]
        by_agent[a]["hit"] += r["cache_hit_tokens"]
        by_agent[a]["miss"] += r["cache_miss_tokens"]
        by_agent[a]["count"] += r["requests"]
    print("按 Agent 命中率:")
    for a, v in sorted(by_agent.items(), key=lambda x: -x[1]["count"]):
        total = v["hit"] + v["miss"]
        rate = round(v["hit"] / total * 100, 2) if total > 0 else None
        print(f"  {a:10s}: hit={v['hit']:>10} miss={v['miss']:>10} "
              f"rate={rate if rate is not None else 'N/A'}% req={v['count']}")
    # 按 task_type 汇总
    by_task = defaultdict(lambda: {"hit": 0, "miss": 0, "count": 0})
    for r in rows:
        t = r["task_type"]
        by_task[t]["hit"] += r["cache_hit_tokens"]
        by_task[t]["miss"] += r["cache_miss_tokens"]
        by_task[t]["count"] += r["requests"]
    print("\n按任务类型命中率:")
    for t, v in sorted(by_task.items(), key=lambda x: -x[1]["count"]):
        total = v["hit"] + v["miss"]
        rate = round(v["hit"] / total * 100, 2) if total > 0 else None
        print(f"  {t:12s}: hit={v['hit']:>10} miss={v['miss']:>10} "
              f"rate={rate if rate is not None else 'N/A'}% req={v['count']}")


if __name__ == "__main__":
    main()
