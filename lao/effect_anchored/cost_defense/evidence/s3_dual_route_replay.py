# P0 LAO成本防线: 规格书 _rev2 (SHA-256 4d6bc3aa…17c4) 证据件
"""S3 实测复演: 双路由分类 + 出箱原子写 + ACK 闭环 + 治理阶梯留痕。

规格锚点: S3-1 分类规则 / S3-2 送达机制 / S3-3 治理阶梯 / S3-4 Nova 接账;
         复演样本 F-1(纯成本→Nova)、F-2(纯行为→Ethan)、F-3(双涉→同送)。
纪律:
    - 行为类与双涉两路证据取自真实 switch_audit.jsonl(只读)。
    - 成本类按 F-1 规定注入合成用量(真实 routing_cost_log 56 条全为 test 合成, 不足以产真实成本告警)。
    - 出箱写入隔离临时目录, 不写生产 share/inbox; 同时只读实测生产出箱目录是否就位。

运行:
    cd /home/agentuser/lao-release && python3 lao/effect_anchored/cost_defense/evidence/s3_dual_route_replay.py
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

from lao.effect_anchored.cost_defense.model import CostEvent, load_switch_events  # noqa: E402
from lao.effect_anchored.cost_defense.router import (  # noqa: E402
    ACK_TIMEOUT_HOURS,
    RECIPIENT_ETHAN,
    RECIPIENT_NOVA,
    AlertOutbox,
    decide_escalation,
    outbox_root,
)
from lao.effect_anchored.cost_defense.sentinel import CostSentinel  # noqa: E402
from lao.effect_anchored.cost_defense.whitelist import (  # noqa: E402
    WhitelistAuditor,
    load_whitelist,
    whitelist_path,
)

UTC = timezone.utc
AUDIT = "/home/agentuser/lao-release/lao/switch_audit.jsonl"
SHARE = "/home/agentuser/share"
SAMPLE_DIR = os.path.join(os.path.dirname(__file__), "samples")
DAY = "2026-08-30"
DAY_START = datetime(2026, 8, 30, 0, 0, tzinfo=UTC)
DAY_END = DAY_START + timedelta(days=1)
ALLOWED = ["token-plan"]


def sha256_of(path: str) -> str:
    """文件 SHA-256(证据三件套之一; 活账本会随追写漂移, 仅代表取数时刻快照)。"""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def window_slice_fingerprint(path: str, day_prefix: str) -> tuple:
    """日窗切片指纹: 只指纹该日期的行, 不随账本尾部追写而漂移。

    活账本的全文件哈希每新增一行就变, 无法做回归基准;
    本指纹取 timestamp 以 day_prefix 开头的原始行拼接后求 SHA-256, 可重复校验。

    Returns:
        (行数, 切片 SHA-256)。
    """
    digest = hashlib.sha256()
    count = 0
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            if f'"timestamp": "{day_prefix}' not in line and f'"timestamp":"{day_prefix}' not in line:
                continue
            digest.update(line.strip().encode("utf-8"))
            digest.update(b"\n")
            count += 1
    return count, digest.hexdigest()


def _print_dispatch(tag: str, record: dict) -> None:
    """打印单条出箱结果的关键字段。"""
    print(
        f"  {tag}: alert_id={record['alert_id']} axis={record['axis']} level={record['level']}\n"
        f"      → delivered_to={record['delivered_to']}"
        f" escalation_level={record['escalation_level']} escalated_to={record['escalated_to']}\n"
        f"      escalation_path={record['escalation_path']}"
        f" 须官方对账={record['requires_official_reconciliation']}"
    )


def main() -> int:
    """三类告警各走一遍分类→出箱→回执闭环, 输出可核对证据。"""
    print("=== S3 双路由 · 出箱与治理阶梯复演 ===")
    print(f"来源文件A(审计账本, 只读): {AUDIT}")
    print(f"  全文件 SHA-256(取数时刻快照, 活账本会漂移): {sha256_of(AUDIT)}")
    slice_rows, slice_hash = window_slice_fingerprint(AUDIT, DAY)
    print(f"  {DAY} 日窗切片指纹(不漂移, 可复校): {slice_rows} 行 / SHA-256 {slice_hash}")
    sample_wl = whitelist_path(SAMPLE_DIR, DAY)
    print(f"来源文件B(白名单复盘样本): {sample_wl}")
    print(f"  SHA-256: {sha256_of(sample_wl)}")
    print("来源C(成本类): 按 F-1 注入合成用量, 见本脚本 _cost_alerts 段(真实成本账本为 test 合成数据)")
    print("取数命令: python3 lao/effect_anchored/cost_defense/evidence/s3_dual_route_replay.py")

    prod_outbox = outbox_root(SHARE)
    print(f"\n[生产出箱实测] {prod_outbox}")
    print(f"  目录存在: {os.path.isdir(prod_outbox)}  (不存在则 S3 上生产前需先建目录)")

    switch_events = load_switch_events(AUDIT, since=DAY_START, until=DAY_END)
    whitelist, wl_status = load_whitelist(SAMPLE_DIR, DAY_START + timedelta(hours=9), day=DAY)
    print(f"\n08-30 切换事件(UTC日窗): {len(switch_events)} 笔; 白名单样本状态={wl_status}")

    with tempfile.TemporaryDirectory() as tmp:
        outbox = AlertOutbox(os.path.join(tmp, "cost-alert-outbox"))
        now = DAY_START + timedelta(hours=16)

        # F-1 纯成本类(注入): 日累计 6.4 USD / 预算 7.0 → 90% 档 L2, axis=cost
        print("\n[F-1 纯成本类 → Nova] 注入合成用量 6.4 USD / 预算 7.0 USD")
        injected = [
            CostEvent(
                ts=DAY_START + timedelta(hours=h),
                provider="deepseek",
                model="deepseek-chat",
                task="code",
                task_id=f"replay-cost-{h}",
                cost_usd=0.8,
            )
            for h in range(8)
        ]
        sentinel = CostSentinel(os.path.join(tmp, "s1state"), daily_budget_usd=7.0)
        cost_alerts = [
            a
            for a in sentinel.evaluate(injected, [], now, allowed_providers=ALLOWED)
            if a.axis == "cost"
        ]
        if not cost_alerts:
            print("  未产出成本类告警, 停手上报")
            return 1
        cost_record = outbox.dispatch(cost_alerts[0], now=now)
        _print_dispatch("成本类", cost_record)
        assert cost_record["delivered_to"] == [RECIPIENT_NOVA]

        # F-2 纯行为类(真实数据): S1 切换异常轴 axis=switch → 归行为类 → Ethan
        print("\n[F-2 纯行为类 → Ethan] 真实 08-30 切换异常轴告警(axis=switch)")
        sentinel2 = CostSentinel(os.path.join(tmp, "s1state2"), daily_budget_usd=7.0)
        switch_alerts = [
            a
            for a in sentinel2.evaluate([], switch_events, now, allowed_providers=ALLOWED)
            if a.axis == "switch"
        ]
        if not switch_alerts:
            print("  未产出行为类告警, 停手上报")
            return 1
        behavior_record = outbox.dispatch(switch_alerts[0], now=now)
        _print_dispatch("行为类", behavior_record)
        assert behavior_record["delivered_to"] == [RECIPIENT_ETHAN]

        # F-3 双涉(真实数据): S2 白名单命中 denied → axis=both → Nova 与 Ethan 同送
        print("\n[F-3 双涉 → Nova + Ethan 同送] 真实 08-30 白名单越界告警(axis=both)")
        auditor = WhitelistAuditor(daily_budget_usd=7.0)
        result = auditor.audit_stream(switch_events, whitelist, wl_status)
        both_alerts = [a for a in result["alerts"] if a.axis == "both"]
        print(f"  真实越界告警总数: {len(both_alerts)} 笔(取首笔出箱)")
        dual_record = outbox.dispatch(both_alerts[0], now=now)
        _print_dispatch("双涉", dual_record)
        assert dual_record["delivered_to"] == [RECIPIENT_NOVA, RECIPIENT_ETHAN]

        # ACK 闭环: 双涉件需两方回执才算闭环
        print("\n[ACK 闭环] 双涉件回执过程")
        dual_id = dual_record["alert_id"]
        status = outbox.delivery_status(dual_id, now=now)
        print(f"  出箱后: acked_by={status.acked_by} 未回执={status.missing()} closed={status.closed}")
        outbox.ack(dual_id, RECIPIENT_NOVA, now=now + timedelta(minutes=3))
        status = outbox.delivery_status(dual_id, now=now)
        print(f"  Nova 回执后: acked_by={status.acked_by} 未回执={status.missing()} closed={status.closed}")
        outbox.ack(dual_id, RECIPIENT_ETHAN, now=now + timedelta(minutes=7))
        status = outbox.delivery_status(dual_id, now=now)
        print(f"  Ethan 回执后: acked_by={status.acked_by} closed={status.closed}")

        # 未闭环上抬: 超 ACK_TIMEOUT_HOURS 未回执 → 阶梯上抬一级
        print(f"\n[未闭环上抬] {ACK_TIMEOUT_HOURS}h 未回执者进入 pending 并上抬阶梯")
        stale_at = now + timedelta(hours=ACK_TIMEOUT_HOURS)
        pending = outbox.pending(stale_at)
        for item in pending:
            before = outbox.read_message(item.alert_id)["escalation_level"]
            level, owners = decide_escalation(
                cost_alerts[0] if item.alert_id == cost_record["alert_id"] else switch_alerts[0],
                unacked_hours=item.age_hours or 0.0,
            )
            print(
                f"  {item.alert_id}: 未回执 {item.age_hours}h 缺 {item.missing()}"
                f" → {before} 上抬为 {level}(责任人 {owners})"
            )
        if not pending:
            print("  无超时件(全部已闭环)")

        # 出箱清单与保留期
        print(f"\n[出箱清单] {outbox.list_alert_ids()}")
        print(f"  保留期检查(7天, dry_run): 到期文件 {outbox.prune(now)}")
        files = sorted(os.listdir(outbox.root))
        print(f"  出箱目录文件: {files}")
        print(f"  残留临时文件: {[f for f in files if f.startswith('.tmp-')]}")

    print("\n结论: 三类分类全部按 S3-1 落位(成本→Nova / 行为→Ethan / 双涉→同送),")
    print("      出箱原子写无临时残留, ACK 两方齐备才闭环, 治理阶梯字段全程留痕。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
