# P0 LAO成本防线: 规格书 _rev2 (SHA-256 4d6bc3aa…17c4) S5 快照污染补救件
"""重建 S5 改动前快照, 并修复审计件的快照覆盖缺陷。

事故如实记录:
    2026-08-31 A类去重 --apply 落盘后, 为出证据复跑了 s5_openclaw_audit.py。
    该审计件 L120 为无条件 `shutil.copy2(OPENCLAW, SNAPSHOT)`, 缺"已存在则不写"
    保护, 遂将**改动后**的 openclaw.json 覆盖写入了名为 pre-s5 的快照文件。
    结果: 快照 SHA 由 44c27e2c…f490(改动前) 变为 b3758421…f772(改动后),
    "改动前快照"这一证据件被污染。责任在施工方选错出证工具且未预先核查其写盘行为。

补救原理(可密码学自证):
    A类去重是纯删除操作, 被删两元素的完整定义与索引均已在 --apply 输出中留痕,
    且已证原文件可由 indent=2/ensure_ascii=False/无尾换行 精确复现。
    故将两元素按原索引、原键序插回, 重新序列化, 若所得 SHA-256 精确等于
    44c27e2c…f490, 即证改动前内容已完整还原 —— 哈希吻合本身就是还原正确的证明,
    无需信任本脚本的自述。

本件写盘范围: 仅 snapshots/ 下快照文件。**绝不触碰 openclaw.json**(开头结尾各
    校验一次其 SHA, 不一致即报)。

运行:
    python3 …/evidence/s5_snapshot_repair.py           # 只重建校验, 不落快照
    python3 …/evidence/s5_snapshot_repair.py --write   # 校验通过才落快照
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile

OPENCLAW = "/home/agentuser/.openclaw/openclaw.json"
REPO = "/home/agentuser/lao-release"
EVID = f"{REPO}/lao/effect_anchored/cost_defense/evidence"
SNAP_DIR = f"{EVID}/snapshots"
SNAPSHOT = f"{SNAP_DIR}/openclaw.json.snapshot-pre-s5-20260831"

PRE_SHA = "44c27e2cfa955f49cf05d74861ee0305132ee3c0e1739c56f9c56bb5c9def490"
POST_SHA = "b375842158ef0f5a0c6e5c90437c267747e84e364caeeb07c5b36de61da3f772"

DUMP_KW = {"indent": 2, "ensure_ascii": False}
TRAILING = ""

# 待插回元素: (provider, 原索引, 元素) — 键序须与原文件一致(id/api/contextWindow/
# maxTokens/name), 取自 --apply 的 unified diff 留痕。
RESTORE = (
    ("novarouteai", 4, {
        "id": "qwen3.8-max",
        "api": "openai-completions",
        "contextWindow": 1000000,
        "maxTokens": 384000,
        "name": "DeepSeek V4 Flash",
    }),
    ("token-plan", 5, {
        "id": "qwen3.8-max",
        "api": "openai-completions",
        "contextWindow": 131072,
        "maxTokens": 8192,
        "name": "DeepSeek V4 Pro (TokenPlan)",
    }),
)


def sha256_of(path: str) -> str:
    """文件 SHA-256。"""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write(path: str, text: str) -> None:
    """原子写: 同目录临时文件 + fsync + os.replace。"""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-repair-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main() -> int:
    """重建改动前内容并以哈希自证; --write 时才落快照。"""
    do_write = "--write" in sys.argv
    print(f"=== S5 快照污染补救 ({'WRITE 落快照' if do_write else '仅校验'}) ===")

    live_sha_open = sha256_of(OPENCLAW)
    print(f"\n-- 现役 openclaw.json (本件绝不改动) --")
    print(f"  SHA-256: {live_sha_open}")
    print(f"  == 改动后应有值: {live_sha_open == POST_SHA}")
    if live_sha_open != POST_SHA:
        print("  [FAIL] 现役状态非预期 — 停手上报, 不做任何重建")
        return 1

    print(f"\n-- 快照现状(污染确认) --")
    snap_now = sha256_of(SNAPSHOT) if os.path.isfile(SNAPSHOT) else "<missing>"
    print(f"  当前 SHA-256: {snap_now}")
    print(f"  应为(改动前): {PRE_SHA}")
    print(f"  已被污染为改动后副本: {snap_now == POST_SHA}")

    with open(OPENCLAW, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    providers = (cfg.get("models") or {}).get("providers") or {}

    print(f"\n-- 重建: 按原索引与原键序插回被删元素 --")
    for pid, idx, elem in RESTORE:
        models = providers[pid]["models"]
        before = len(models)
        models.insert(idx, dict(elem))
        print(f"  {pid}.models: {before} → {len(models)} 项, "
              f"插回索引[{idx}] name={elem['name']!r}")
        print(f"    落位核验: 索引[{idx}].name == {models[idx].get('name')!r} / "
              f"id == {models[idx].get('id')!r}")

    rebuilt = json.dumps(cfg, **DUMP_KW) + TRAILING
    rebuilt_sha = hashlib.sha256(rebuilt.encode("utf-8")).hexdigest()

    print(f"\n-- 哈希自证 --")
    print(f"  重建物 SHA-256: {rebuilt_sha}")
    print(f"  改动前 SHA-256: {PRE_SHA}")
    ok = rebuilt_sha == PRE_SHA
    print(f"  逐字节还原: {'PASS — 改动前内容已完整还原' if ok else 'FAIL'}")
    print(f"  行数: {len(rebuilt.splitlines())} (改动前应为 1323)")
    print(f"  字节: {len(rebuilt.encode())} (改动前应为 33444)")
    print(f"  qwen3.8-max 计数: {rebuilt.count('qwen3.8-max')} (改动前应为 24)")
    if not ok:
        print("  [FAIL] 重建物与改动前哈希不符 — 不写快照, 停手上报")
        return 1

    if not do_write:
        print(f"\n=== 仅校验完毕: 未写任何文件 ===")
        print(f"  openclaw.json 复核: {sha256_of(OPENCLAW)}")
        print(f"  全程未改: {sha256_of(OPENCLAW) == live_sha_open}")
        return 0

    _atomic_write(SNAPSHOT, rebuilt)
    print(f"\n-- 快照已复原 --")
    print(f"  {SNAPSHOT}")
    print(f"  SHA-256: {sha256_of(SNAPSHOT)}")
    print(f"  == 改动前应有值: {sha256_of(SNAPSHOT) == PRE_SHA}")
    leftovers = [f for f in os.listdir(SNAP_DIR) if f.startswith(".tmp-repair-")]
    print(f"  残留临时文件: {leftovers}")

    print(f"\n-- 收尾: openclaw.json 未被本件触碰 --")
    print(f"  SHA-256: {sha256_of(OPENCLAW)}")
    print(f"  全程未改: {sha256_of(OPENCLAW) == live_sha_open}")
    print(f"\n[遗留缺陷待修] s5_openclaw_audit.py L120 无条件 copy2 覆盖快照,")
    print(f"  须改为'快照已存在则跳过并只校验', 否则任何复跑都会再次污染。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
