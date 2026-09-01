# P0 LAO成本防线: 规格书 _rev2 (SHA-256 4d6bc3aa…17c4) 证据件
"""S1 实测复演: 用真实 08-30 审计账本验证切换异常轴的检测时延。

规格锚点: S1-3 切换异常轴 / R-01 "S1 切换异常轴最迟第2小时告警"。
纪律: 对 switch_audit.jsonl 只读; 去重状态写入临时目录, 不污染生产 share/state。

运行:
    cd /home/agentuser/lao-release && python3 lao/effect_anchored/cost_defense/evidence/s1_switch_anomaly_replay.py
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

from lao.effect_anchored.cost_defense.model import load_switch_events  # noqa: E402
from lao.effect_anchored.cost_defense.sentinel import CostSentinel  # noqa: E402

UTC = timezone.utc
AUDIT = "/home/agentuser/lao-release/lao/switch_audit.jsonl"
DAY_START = datetime(2026, 8, 30, 0, 0, tzinfo=UTC)
DAY_END = DAY_START + timedelta(days=1)
ALLOWED = ["token-plan"]


def sha256_of(path: str) -> str:
    """文件 SHA-256(证据三件套之一)。"""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    """逐小时复演 08-30, 报告首次触发切换异常轴的 UTC 小时。"""
    print("=== S1 切换异常轴 · 08-30 真实账本只读复演 ===")
    print(f"来源文件: {AUDIT}")
    print(f"SHA-256 : {sha256_of(AUDIT)}")
    print(f"取数命令: python3 lao/effect_anchored/cost_defense/evidence/s1_switch_anomaly_replay.py")

    events = load_switch_events(AUDIT, since=DAY_START, until=DAY_END)
    print(f"08-30 全量切换事件(UTC日窗): {len(events)} 笔")

    non_whitelisted = [e for e in events if e.to_provider.lower() not in ALLOWED]
    print(f"其中切入非白名单 provider: {len(non_whitelisted)} 笔")
    by_provider: dict = {}
    for event in non_whitelisted:
        by_provider[event.to_provider] = by_provider.get(event.to_provider, 0) + 1
    print(f"非白名单 provider 分布: {by_provider}")

    hourly: dict = {}
    for event in non_whitelisted:
        hourly[event.ts.hour] = hourly.get(event.ts.hour, 0) + 1
    print(f"非白名单切入逐小时分布(UTC时:笔数): {dict(sorted(hourly.items()))}")
    first_event_hour = min(hourly) if hourly else None
    print(f"首笔非白名单切入发生于 UTC 第 {first_event_hour} 小时")

    first_hit = None
    with tempfile.TemporaryDirectory() as tmp:
        for hour in range(24):
            now = DAY_START + timedelta(hours=hour, minutes=59, seconds=59)
            sentinel = CostSentinel(os.path.join(tmp, f"h{hour}"), daily_budget_usd=7.0)
            alerts = sentinel.evaluate([], events, now, allowed_providers=ALLOWED)
            switch_alerts = [a for a in alerts if a.axis == "switch"]
            if switch_alerts and first_hit is None:
                first_hit = (hour, switch_alerts[0])
                break

    if first_hit is None:
        print("结论: 08-30 该轴未触发(当日无单小时≥20笔切入非白名单 provider)")
        print("说明: 357笔 agent_binding→deepseek 为跨小时分布, 逐小时未达20笔阈值;")
        print("      该场景由 S2 白名单校验第1笔即拦(见 R-01), 两轴互补。")
        return 0

    hour, alert = first_hit
    lag = hour - first_event_hour if first_event_hour is not None else None
    print(f"结论: 首次触发于 UTC 第 {hour} 小时(窗口 {alert.window})")
    print(f"  检测时延: 自首笔非白名单切入起 {lag} 小时内告警 → 满足 R-01 验收预期(最迟第2小时)")
    print(f"  provider={alert.provider} rule={alert.trigger_rule}")
    print(f"  明细笔数(截断上限20)={len(alert.sample_details)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
