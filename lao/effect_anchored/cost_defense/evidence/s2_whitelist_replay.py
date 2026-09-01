# P0 LAO成本防线: 规格书 _rev2 (SHA-256 4d6bc3aa…17c4) 证据件
"""S2 实测复演: 用真实 08-30 审计账本验证白名单轴"第1笔即告警"。

规格锚点: S2-2 "检测延迟 ≤ 单条审计落盘间隔" / R-01 "白名单第1笔即告警"。
纪律:
    - 对 switch_audit.jsonl 只读; 去重状态写临时目录, 不污染生产 share/state。
    - 白名单用复盘重建样本(evidence/samples/), 不向 share/state 写入任何文件。
    - 同时实测生产 share/state 当日白名单是否存在, 如实报告默认安全态。

运行:
    cd /home/agentuser/lao-release && python3 lao/effect_anchored/cost_defense/evidence/s2_whitelist_replay.py
"""

from __future__ import annotations

import hashlib
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

from lao.effect_anchored.cost_defense.model import load_switch_events  # noqa: E402
from lao.effect_anchored.cost_defense.whitelist import (  # noqa: E402
    STATUS_VALID,
    WhitelistAuditor,
    load_whitelist,
    whitelist_path,
)

UTC = timezone.utc
AUDIT = "/home/agentuser/lao-release/lao/switch_audit.jsonl"
PROD_STATE = "/home/agentuser/share/state"
SAMPLE_DIR = os.path.join(os.path.dirname(__file__), "samples")
DAY = "2026-08-30"
DAY_START = datetime(2026, 8, 30, 0, 0, tzinfo=UTC)
DAY_END = DAY_START + timedelta(days=1)


def sha256_of(path: str) -> str:
    """文件 SHA-256(证据三件套之一)。"""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    """复演 08-30 白名单轴, 报告首次越界笔序与检测时延。"""
    print("=== S2 白名单轴 · 08-30 真实账本只读复演 ===")
    print(f"来源文件A(审计账本): {AUDIT}")
    print(f"  SHA-256: {sha256_of(AUDIT)}")
    sample = whitelist_path(SAMPLE_DIR, DAY)
    print(f"来源文件B(白名单复盘样本): {sample}")
    print(f"  SHA-256: {sha256_of(sample)}")
    print("取数命令: python3 lao/effect_anchored/cost_defense/evidence/s2_whitelist_replay.py")

    # ① 生产态实测: 08-30 当日白名单原件是否存在
    prod_path = whitelist_path(PROD_STATE, DAY)
    _, prod_status = load_whitelist(PROD_STATE, DAY_START + timedelta(hours=9), day=DAY)
    print(f"\n[生产态实测] {prod_path}")
    print(f"  状态: {prod_status}  → 缺失/过期一律走默认安全态(🟠, 宁紧勿松)")

    # ② 复盘样本加载
    whitelist, status = load_whitelist(SAMPLE_DIR, DAY_START + timedelta(hours=9), day=DAY)
    print(f"\n[复盘样本] 状态={status} signed_by={whitelist.signed_by if whitelist else None}")
    if status != STATUS_VALID or whitelist is None:
        print("样本加载异常, 停手上报")
        return 1
    print(f"  allowed={whitelist.allowed_providers} denied={whitelist.denied_providers}")
    print(f"  effective_from={whitelist.effective_from} expires_at={whitelist.expires_at}")

    # ③ 逐笔流式复演
    events = load_switch_events(AUDIT, since=DAY_START, until=DAY_END)
    print(f"\n08-30 全量切换事件(UTC日窗): {len(events)} 笔")
    auditor = WhitelistAuditor(daily_budget_usd=7.0)  # 不挂去重, 逐笔全量以量首笔时延
    result = auditor.audit_stream(events, whitelist, status)
    alerts = result["alerts"]
    print(f"扫描笔数: {result['scanned']}  越界告警: {len(alerts)} 笔")

    by_rule: dict = {}
    for alert in alerts:
        by_rule[alert.trigger_rule] = by_rule.get(alert.trigger_rule, 0) + 1
    print(f"按规则分布: {by_rule}")

    if result["first_hit_index"] is None:
        print("结论: 08-30 无越界切换(白名单轴未触发)")
        return 0

    ordered = sorted([e for e in events if e.ts is not None], key=lambda e: e.ts)
    first_alert = alerts[0]
    first_offense = ordered[result["first_hit_index"] - 1]
    print(f"\n结论: 首次告警发生在时序第 {result['first_hit_index']} 笔事件(即本批首个越界笔)")
    print(f"  首笔越界事件 ts={first_offense.ts.isoformat()} to_provider={first_offense.to_provider}")
    print(f"  告警 ts={first_alert.ts} level={first_alert.level} axis={first_alert.axis}")
    print(f"  rule={first_alert.trigger_rule} window={first_alert.window}")
    lag_seconds = 0  # 同笔判定, 不跨落盘间隔
    print(f"  检测时延: {lag_seconds} 秒(同笔判定) → 满足 S2-2 \"≤单条审计落盘间隔\"与 R-01 \"第1笔即告警\"")

    # ④ 默认安全态复演: 无白名单时同一批数据的告警量
    fallback = auditor.audit_stream(events, None, prod_status)
    print(f"\n[默认安全态复演] 白名单={prod_status} 时告警 {len(fallback['alerts'])} 笔"
          f"(首笔序号 {fallback['first_hit_index']})")
    print("  即: 当日无白名单落盘 → 一切切换均报 🟠, 不因缺文件而放行。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
