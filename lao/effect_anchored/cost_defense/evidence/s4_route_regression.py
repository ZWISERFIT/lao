# P0 LAO成本防线: 规格书 _rev2 (SHA-256 4d6bc3aa…17c4) 证据件
"""S4 路由回归复演: 清理前后路由行为逐字节比对(只读, 零写盘)。

规格锚点: S4-2 清理后"路由行为回归测试通过"; 六·R-03/R-04 路由回归口径。

复演口径(为何这样设计):
    S4 唯一的活代码改动是从 _build_dynamic_pool 的 token-plan 候选元组中移除
    qwen3.8-max。它影响的是**池组成**, 因此回归必须同时证明两件事:
        A. 池里确实不再有 qwen3.8-max        (清理达成)
        B. 每个 tier 的最终选品与降级取值分毫未变 (行为中立)
    B 由"行为指纹"承担: 只要该指纹清理前后 SHA-256 逐字节一致, 即证明
    选路行为未变; 池组成变化被单独记在"池指纹"里, 允许且仅允许该处差异。

零写盘保证:
    - 只调用 _build_dynamic_pool()(纯读 openclaw.json) 与
      select_optimal()(对 pool 列表的纯函数), 不调用会触发 _audit_switch 的
      整条 select 链路 —— 后者会向活账本 switch_audit.jsonl 追写记录。
    - 降级取值按 model_router.py L713-731 的同一规则在本脚本内就地推导
      (_flash / _from), 不经审计写盘出口。
    - 运行前后各取一次活账本指纹, 不一致即判本次复演无效。

运行(清理前取基准, 清理后再取一次, 两份输出直接 diff):
    cd /home/agentuser/lao-release && python3 lao/effect_anchored/cost_defense/evidence/s4_route_regression.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

from lao.effect_anchored.routing.model_router import ModelRouter  # noqa: E402

TARGET = "qwen3.8-max"
ROUTER_SRC = "/home/agentuser/lao-release/lao/effect_anchored/routing/model_router.py"
BASELINE_SRC = ROUTER_SRC + ".bak-hemostasis-20260830"
AUDIT = "/home/agentuser/lao-release/lao/switch_audit.jsonl"
DAY = "2026-08-30"
DEAD_PROVIDER = "token-plan"

TIERS = ("ultra_light", "light", "medium", "heavy", "reasoning",
         "code", "cn_explain", "cn_creative")


def sha256_of(path: str) -> str:
    """文件 SHA-256。"""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def window_slice_fingerprint(path: str, day_prefix: str) -> tuple:
    """日窗切片指纹(S3 沿用): 只指纹目标日期行, 不随账本追写漂移。"""
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


def _label(entry: dict) -> str:
    """池条目的稳定标签。"""
    return f"{entry.get('model', '')}@{entry.get('provider', '')}"


def _degrade_values(chain: list) -> dict:
    """就地复现 L713-731 配额降级的取值规则(不写审计)。

    方案A: 同 provider(token-plan) flash 档降级;
    审计 from = 被降级 provider 首个非 flash 档; to = 首个 flash 档。
    """
    dead_flash = [e for e in chain
                  if e.get("provider") == DEAD_PROVIDER
                  and "flash" in str(e.get("model", "")).lower()]
    dead_nonflash = [e for e in chain
                     if e.get("provider") == DEAD_PROVIDER
                     and "flash" not in str(e.get("model", "")).lower()]
    alive = [e for e in chain if e.get("provider") != DEAD_PROVIDER]
    plan = "A" if dead_flash else ("C" if alive else "保底原池")
    return {
        "plan": plan,
        "degrade_from": _label(dead_nonflash[0]) if dead_nonflash else (
            _label(chain[0]) if chain else ""),
        "degrade_to": _label(dead_flash[0]) if dead_flash else (
            _label(alive[0]) if alive else ""),
    }


def collect(router: ModelRouter) -> dict:
    """采集池组成 + 行为两组数据。"""
    pool = router._build_dynamic_pool()
    pool_view: dict = {}
    behavior: dict = {}
    for tier in TIERS:
        chain = pool.get(tier, [])
        pool_view[tier] = [_label(e) for e in chain]
        chosen = router.select_optimal(list(chain), tier) if chain else {}
        row = {"chosen": _label(chosen) if chosen else ""}
        row.update(_degrade_values(chain))
        behavior[tier] = row
    return {"pool": pool_view, "behavior": behavior}


def _fingerprint(payload: object) -> str:
    """规范化 JSON 后求 SHA-256(可逐字节比对)。"""
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=None,
                      separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> int:
    """输出路由行为指纹与池指纹, 供清理前后比对。"""
    print("=== S4 路由回归复演 (R-03/R-04 路由回归口径·只读零写盘) ===")
    print(f"现役路由器: {ROUTER_SRC}")
    print(f"  SHA-256: {sha256_of(ROUTER_SRC)}")
    print(f"止血基线(只读不动): SHA-256 {sha256_of(BASELINE_SRC)}")

    rows_before, hash_before = window_slice_fingerprint(AUDIT, DAY)
    audit_full_before = sha256_of(AUDIT)
    print(f"活账本前置指纹: {DAY} 切片 {rows_before} 行 / {hash_before}")

    router = ModelRouter()
    data = collect(router)

    print(f"\n[池组成] (允许且仅允许 {TARGET} 一项差异)")
    target_hits = 0
    for tier in TIERS:
        chain = data["pool"][tier]
        target_hits += sum(1 for label in chain if TARGET in label)
        print(f"  {tier}: {chain}")
    print(f"  池内 {TARGET} 出现次数: {target_hits}")

    print("\n[路由行为] (清理前后必须逐字节一致)")
    for tier in TIERS:
        row = data["behavior"][tier]
        print(f"  {tier}: 选品={row['chosen']} 降级方案={row['plan']}"
              f" from={row['degrade_from']} to={row['degrade_to']}")

    pool_fp = _fingerprint(data["pool"])
    behavior_fp = _fingerprint(data["behavior"])
    print(f"\n池指纹      SHA-256: {pool_fp}")
    print(f"行为指纹    SHA-256: {behavior_fp}   ← 通过标准: 清理前后此值不变")

    rows_after, hash_after = window_slice_fingerprint(AUDIT, DAY)
    audit_full_after = sha256_of(AUDIT)
    clean = (rows_after == rows_before and hash_after == hash_before
             and audit_full_after == audit_full_before)
    print(f"\n活账本后置指纹: {DAY} 切片 {rows_after} 行 / {hash_after}")
    print(f"本次复演零写盘: {clean}  (False 则本次复演无效, 立即停手上报)")
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
