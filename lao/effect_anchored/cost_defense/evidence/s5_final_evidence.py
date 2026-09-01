# P0 LAO成本防线: 规格书 _rev2 (SHA-256 4d6bc3aa…17c4) S5 终验收证据采集件
"""终验收单底数采集(严格只读): 账本损坏行定位 + build/lib 镜像偏差 + 成本账本实况。

采集项(每项均给出来源文件 + SHA-256 + 可复跑命令, 满足验收三件套要求):
    E1 switch_audit.jsonl 逐行 JSON 解析, 定位损坏行号与损坏形态(核验"1303行损坏")
    E2 routing_cost_log.json 实况(成本轴 F-1 是否有真实生产数据)
    E3 build/lib 镜像与现役 model_router.py 的 SHA 及 qwen3.8-max 命中差异
    E4 生产出箱目录 / 生产态白名单 落地状态(跟进项底数)

只读边界: 全程 open(..., "r"), 不写任何文件, 不改任何账本与配置。

运行:
    python3 /home/agentuser/lao-release/lao/effect_anchored/cost_defense/evidence/s5_final_evidence.py
"""

from __future__ import annotations

import hashlib
import json
import os

REPO = "/home/agentuser/lao-release"
AUDIT = f"{REPO}/lao/switch_audit.jsonl"
COST_LOG = f"{REPO}/lao/routing_cost_log.json"
ROUTER = f"{REPO}/lao/effect_anchored/routing/model_router.py"
MIRROR = f"{REPO}/build/lib/lao/effect_anchored/routing/model_router.py"
OUTBOX = "/home/agentuser/share/inbox/cost-alert-outbox"
PROD_WL = "/home/agentuser/share/state/provider-daily-whitelist-2026-08-30.json"
TARGET = "qwen3.8-max"


def sha256_of(path: str) -> str:
    """文件 SHA-256; 文件缺失返回 <missing>。"""
    if not os.path.isfile(path):
        return "<missing>"
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def count_lines_with(path: str) -> int:
    """含 TARGET 的行数; 文件缺失返回 -1。"""
    if not os.path.isfile(path):
        return -1
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return sum(1 for line in fh if TARGET in line)


def e1_audit_integrity() -> None:
    """逐行解析审计账本, 如实报告总行数与全部损坏行。"""
    print("=== E1 switch_audit.jsonl 完整性 ===")
    print(f"来源: {AUDIT}")
    print(f"SHA-256(取数时刻, 活账本会漂移): {sha256_of(AUDIT)}")
    total = 0
    broken = []
    with open(AUDIT, "r", encoding="utf-8", errors="replace") as fh:
        for no, line in enumerate(fh, 1):
            total += 1
            stripped = line.strip()
            if not stripped:
                broken.append((no, "空行", ""))
                continue
            try:
                json.loads(stripped)
            except json.JSONDecodeError as exc:
                broken.append((no, f"JSONDecodeError: {exc.msg} (col {exc.colno})",
                               stripped[:80]))
    print(f"总行数: {total}")
    print(f"损坏行数: {len(broken)}")
    for no, why, head in broken:
        print(f"  行 {no}: {why}")
        print(f"        首80字符: {head!r}")
    if not broken:
        print("  (无损坏行 — 与'1303行损坏'的记载不符, 须以本次实测为准复核记载)")


def e2_cost_log() -> None:
    """成本账本实况: 是否存在真实生产用量。"""
    print("\n=== E2 routing_cost_log.json 实况 ===")
    print(f"来源: {COST_LOG}")
    print(f"存在: {os.path.isfile(COST_LOG)}")
    print(f"SHA-256: {sha256_of(COST_LOG)}")
    if not os.path.isfile(COST_LOG):
        return
    print(f"字节数: {os.path.getsize(COST_LOG)}")
    with open(COST_LOG, "r", encoding="utf-8", errors="replace") as fh:
        raw = fh.read()
    print(f"行数: {len(raw.splitlines())}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"解析失败: {exc}")
        return
    if isinstance(data, dict):
        print(f"顶层键: {sorted(data)}")
        for key in sorted(data):
            val = data[key]
            kind = type(val).__name__
            size = len(val) if isinstance(val, (list, dict)) else val
            print(f"  {key}: {kind} = {size}")
    elif isinstance(data, list):
        print(f"数组长度: {len(data)}")


def e3_mirror() -> None:
    """build/lib 镜像与现役的偏差(S4 跟进项底数)。"""
    print("\n=== E3 build/lib 镜像偏差 ===")
    print(f"现役: {ROUTER}")
    print(f"  SHA-256: {sha256_of(ROUTER)}")
    print(f"  {TARGET} 命中行: {count_lines_with(ROUTER)}")
    print(f"镜像: {MIRROR}")
    print(f"  存在: {os.path.isfile(MIRROR)}")
    print(f"  SHA-256: {sha256_of(MIRROR)}")
    print(f"  {TARGET} 命中行: {count_lines_with(MIRROR)}")
    print(f"两者一致: {sha256_of(ROUTER) == sha256_of(MIRROR)}")
    print("  说明: 镜像为构建产物, 非现役执行路径; 本次未动(范围仅限4处裁定)")


def e4_prod_landing() -> None:
    """生产侧落地状态(出箱目录 / 生产态白名单)。"""
    print("\n=== E4 生产侧落地状态 ===")
    print(f"出箱目录: {OUTBOX}")
    print(f"  存在: {os.path.isdir(OUTBOX)}")
    if os.path.isdir(OUTBOX):
        print(f"  文件数: {len(os.listdir(OUTBOX))}")
    print(f"生产态白名单: {PROD_WL}")
    print(f"  存在: {os.path.isfile(PROD_WL)}")
    print(f"  SHA-256: {sha256_of(PROD_WL)}")
    print("  说明: 缺失时 whitelist 走默认安全态(宁紧勿松), 不静默放行")


def main() -> int:
    """依次采集 E1~E4。"""
    e1_audit_integrity()
    e2_cost_log()
    e3_mirror()
    e4_prod_landing()
    print("\n=== 采集完毕(全程只读, 未写任何文件) ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
