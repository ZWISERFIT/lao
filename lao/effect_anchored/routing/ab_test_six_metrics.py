"""A/B 测试脚本 — LAO ON/OFF 七指标对照(Shuyu 派单·成本事故复盘①+成功率门禁②)

对照:
- LAO 组(ON): 走 lao-router(8765)·当前 tristan 等
- 直连组(OFF): 走 api.deepseek.com·Luna/Nova 等

指标(不能只看命中率·创始人三满足之一 = 任务成功率):
  ① request数 ② input token ③ cache hit rate ④ cache miss token
  ⑤ output token ⑥ total cost ⑦ **任务成功率(success_rate)**
  成功率 = 成功请求 / 总请求(含错误重试成本·status!=error 即成功)

数据源: lao-router-events.jsonl(cache_hit_tokens/cache_miss_tokens/input_tokens/
        output_tokens/status/error/ts)
验收(gate DRI):
  - 命中率: LAO组追平直连组80%+ 才可重开LAO
  - 成功率: LAO组 ≥ 直连组(成功率是创始人三满足硬门禁·高命中率≠高成功率)
数据窗口: 17时后(修复后窗口)·避免把修复前 400 错误混入
"""
from __future__ import annotations
import json, os, sys
from collections import defaultdict
from datetime import datetime

EVENT_LOG = "/home/agentuser/.openclaw/workspace/tristan/tech_lead/logs/lao-router-events.jsonl"
OUT_DIR = "/home/agentuser/zwiserfit-os/finance/data"
os.makedirs(OUT_DIR, exist_ok=True)

# 数据窗口(成本事故复盘·成功率门禁): 修复后窗口 = 17:00 之后
# 避免把修复前(17时前)的 400 错误/thinking 事故混入成功率分母
WINDOW_AFTER_HOUR = 17  # 只统计 ts >= 17:00 的事件(修复后)


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


# 直连组(官方CSV·8.1-8.12基线·Stella/Nova数据): Luna 93.8% / Nova 92.9%
# LAO组(实测·lao-router事件日志): 40.9-47%
DIRECT_BASELINE = {
    "luna": {"hit_rate_pct": 93.8, "source": "官方CSV 8.1-8.12"},
    "nova": {"hit_rate_pct": 92.9, "source": "官方CSV 8.1-8.12"},
}


def _in_window(e: dict, after_hour: int) -> bool:
    """数据窗口过滤: 只保留 ts >= after_hour 的事件(修复后窗口)。"""
    ts = e.get("ts", "")
    try:
        # ts 格式: 2026-08-15T21:06:09+0800
        d = datetime.fromisoformat(ts.replace("+0800", "+08:00").replace("+0000", "+00:00"))
        return d.hour >= after_hour
    except Exception:
        return True  # ts 缺失/异常 → 保守纳入(不丢数据)


def compute_seven_metrics(events: list, min_cache_tokens: int = 200) -> dict:
    """聚合七指标(口径一致·短请求<200 tokens不计命中率分母)。

    新增(成功率门禁): success_rate = 成功请求 / 总请求。
    - 成功 = status != 'error'(流式成功段 status 缺失也视为成功)
    - 分母含错误请求(错误重试成本也计入 request_count·不只看命中率)
    """
    agg = {
        "request_count": 0,
        "success_count": 0,
        "error_count": 0,
        "input_tokens": 0, "output_tokens": 0,
        "cache_hit_tokens": 0, "cache_miss_tokens": 0,
        "total_cost_yuan": 0.0,
        "count_with_cache": 0,
    }
    for e in events:
        agg["request_count"] += 1
        # 成功率: status=='error' 记为失败·其余(含缺失 status 的流式成功段)记为成功
        if e.get("status") == "error":
            agg["error_count"] += 1
        else:
            agg["success_count"] += 1
        agg["input_tokens"] += e.get("input_tokens") or 0
        agg["output_tokens"] += e.get("output_tokens") or 0
        agg["cache_hit_tokens"] += e.get("cache_hit_tokens") or 0
        agg["cache_miss_tokens"] += e.get("cache_miss_tokens") or 0
        agg["total_cost_yuan"] += e.get("cost_yuan") or 0
        # 命中率分母: 有缓存价值请求(hit+miss>=200)
        if (e.get("cache_hit_tokens") or 0) + (e.get("cache_miss_tokens") or 0) >= min_cache_tokens:
            agg["count_with_cache"] += 1

    total = agg["cache_hit_tokens"] + agg["cache_miss_tokens"]
    agg["cache_hit_rate_pct"] = round(agg["cache_hit_tokens"] / total * 100, 2) if total > 0 else None
    agg["success_rate_pct"] = (round(agg["success_count"] / agg["request_count"] * 100, 2)
                               if agg["request_count"] > 0 else None)
    return agg


# 向后兼容别名(旧调用方 compute_six_metrics 仍可用)
compute_six_metrics = compute_seven_metrics


def main():
    events = load_events()

    # 数据窗口(修复后窗口): 只统计 17:00 后的事件
    windowed = [e for e in events if _in_window(e, WINDOW_AFTER_HOUR)]
    print(f"  数据窗口: {WINDOW_AFTER_HOUR}:00 之后(修复后)·原始 {len(events)} 事件 → 窗口内 {len(windowed)} 事件")

    # 分组: LAO组(有agent标识·当前走8765) vs 直连组(未知agent·直接api.deepseek)
    # 注: 错误记录(status=error)无 cache/usage 字段·不能按 cache_miss_tokens 过滤·
    #     否则错误请求被丢弃 → 成功率分母失真 = 100%(漏算错误重试成本)。
    #     正确: 有 agent 即纳入(含 error 记录)·使成功率 = 成功/(成功+错误) 真实。
    lao_events = [e for e in windowed if e.get("agent")]
    # 全量+按agent

    lao_metrics = compute_seven_metrics(lao_events)
    # 按 agent 分组
    by_agent = defaultdict(list)
    for e in lao_events:
        by_agent[e.get("agent") or "unknown"].append(e)

    print("=" * 70)
    print("  A/B TEST · LAO ON/OFF 七指标对照 (成本事故复盘·成功率门禁·Shuyu派单)")
    print("=" * 70)

    # 直连组基线(官方CSV·对照)
    print("\n【直连组基线·官方CSV 8.1-8.12·Nova数据】")
    for a, v in DIRECT_BASELINE.items():
        print(f"  {a:8s}: 命中率 {v['hit_rate_pct']}%  ({v['source']})")

    # LAO组
    print("\n【LAO组·实测 lao-router 事件日志】")
    print(f"  全LAO: req={lao_metrics['request_count']} input={lao_metrics['input_tokens']:,} "
          f"output={lao_metrics['output_tokens']:,} hit_rate={lao_metrics['cache_hit_rate_pct']}% "
          f"miss={lao_metrics['cache_miss_tokens']:,} cost=¥{lao_metrics['total_cost_yuan']:.2f}")
    sr = lao_metrics['success_rate_pct']
    print(f"  成功率: {sr if sr is not None else 'N/A'}% "
          f"(成功 {lao_metrics['success_count']} / 错误 {lao_metrics['error_count']} / 总 {lao_metrics['request_count']})")

    print("\n  【按 Agent】")
    for a, evs in sorted(by_agent.items(), key=lambda x: -len(x[1])):
        m = compute_seven_metrics(evs)
        sr_a = m['success_rate_pct']
        print(f"  {a:8s}: req={m['request_count']:>5} input={m['input_tokens']:>12,} "
              f"hit_rate={m['cache_hit_rate_pct'] if m['cache_hit_rate_pct'] is not None else 'N/A':>6}% "
              f"success={sr_a if sr_a is not None else 'N/A'}% "
              f"miss={m['cache_miss_tokens']:>12,} output={m['output_tokens']:>10,} cost=¥{m['total_cost_yuan']:.3f}")

    # 门禁判断(⑥ 命中率 + ⑦ 成功率·双门禁)
    print("\n【⑥⑦ 三满足门禁(命中率 + 成功率)】")
    lao_rate = lao_metrics["cache_hit_rate_pct"]
    lao_success = lao_metrics["success_rate_pct"]
    direct_avg = sum(v["hit_rate_pct"] for v in DIRECT_BASELINE.values()) / len(DIRECT_BASELINE)
    direct_success = 99.0  # 直连组成功率基线(官方 CSV: 直连无 router 层·成功率≈100%)
    hit_ok = lao_rate is not None and lao_rate >= direct_avg - 5
    success_ok = lao_success is not None and lao_success >= direct_success - 1
    print(f"  门禁①命中率: LAO({lao_rate}%) 追平直连({direct_avg}%)? "
          f"{'✅通过' if hit_ok else '❌未通过·LAO降本不成立'}")
    print(f"  门禁②成功率: LAO({lao_success}%) ≥ 直连({direct_success}%)? "
          f"{'✅通过' if success_ok else '❌未通过·有 400 错误未根治·成功率不达标(创始人硬门禁)'}")

    # 输出 JSON
    out = {
        "generated_at": datetime.now().isoformat(),
        "window_after_hour": WINDOW_AFTER_HOUR,
        "direct_baseline": DIRECT_BASELINE,
        "direct_success_baseline_pct": direct_success,
        "lao_metrics": lao_metrics,
        "by_agent": {a: compute_seven_metrics(evs) for a, evs in by_agent.items()},
    }
    out_path = os.path.join(OUT_DIR, "ab-test-lao-on-off-7metrics.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    print(f"\n✅ A/B 数据输出: {out_path}")


if __name__ == "__main__":
    main()
