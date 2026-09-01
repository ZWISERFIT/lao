# P0 LAO成本防线: 规格书 _rev2 (SHA-256 4d6bc3aa…17c4) 裁定② build/lib 镜像同步件
"""裁定②: 将 build/lib 镜像的 model_router.py 同步为现役版, 并作为发版前校验门。

背景:
    build/lib 是构建产物镜像, 非现役执行路径。实测其 model_router.py 的 SHA 恰等于
    止血基线 68f7aaa7…68351, 即镜像内容为**止血前版本**, 仍含 4 处 qwen3.8-max。
    若据此镜像重新打包发版, 会把 S4 已清理的 4 处声明重新带回 —— 即回退 S4 清理。

裁定②处置:
    ① 镜像同步为现役版(6df631e6…9d66); ② 哈希登台账; ③ 处置门 = 发版前校验。
    本件同时承担 ①③: 默认只校验(可作发版前门), --apply 才执行同步。

为何覆盖镜像不灭失证据(关键安全论证):
    镜像当前内容与止血基线 .bak-hemostasis-20260830 **逐字节相同**(同 SHA)。
    该基线受铁律①保护且由 G3 门槛专项守护, 永不删改。故镜像被覆盖后, 其原内容
    仍完整留存于基线文件中, 可随时取回 —— 覆盖不造成任何证据灭失。
    闸2 会强制校验这一同源关系, 不成立即拒改停手。

只写一个文件: build/lib/lao/effect_anchored/routing/model_router.py
绝不触碰: 现役 router / .bak-hemostasis-* / .snapshot-* / 任何活配置与活账本

运行:
    python3 …/evidence/s5_mirror_sync.py           # 只校验(发版前门), 不写盘
    python3 …/evidence/s5_mirror_sync.py --apply   # 执行同步
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

TARGET = "qwen3.8-max"
REPO = "/home/agentuser/lao-release"
LIVE = f"{REPO}/lao/effect_anchored/routing/model_router.py"
MIRROR = f"{REPO}/build/lib/lao/effect_anchored/routing/model_router.py"
BASELINE = LIVE + ".bak-hemostasis-20260830"
SNAPSHOT = LIVE + ".snapshot-pre-s4-20260831"

LIVE_SHA = "6df631e6fbca7720c14d2d1c3204f42b2310a09e60f2a17500a7acace9b39d66"
MIRROR_PRE_SHA = "68f7aaa7c8d1d4ffb153aab2563e45017942b1831513f3672d8d6a332ca68351"
BASELINE_SHA = MIRROR_PRE_SHA  # 镜像原内容 == 止血基线, 故覆盖不灭失证据
FORBIDDEN = (LIVE, BASELINE, SNAPSHOT)


def sha256_of(path: str) -> str:
    """文件 SHA-256。"""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def count_lines(path: str) -> int:
    """含 TARGET 的行数(等价 grep -c)。"""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return sum(1 for line in fh if TARGET in line)


def guard_forbidden(snap: dict) -> bool:
    """收尾核对: 禁改清单内文件一律逐字节未变。"""
    ok = True
    for path in FORBIDDEN:
        now = sha256_of(path)
        same = now == snap[path]
        print(f"  {'[OK]  ' if same else '[BAD] '}{os.path.basename(path)}: "
              f"{'未变' if same else '已变 — 铁律告警'}")
        ok = ok and same
    return ok


def atomic_copy(src: str, dst: str) -> None:
    """原子替换 dst 为 src 内容, 保留原权限位。"""
    dirn = os.path.dirname(dst)
    mode = os.stat(dst).st_mode
    fd, tmp = tempfile.mkstemp(dir=dirn, prefix=".mirror-sync-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as out, open(src, "rb") as fin:
            shutil.copyfileobj(fin, out)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(tmp, mode & 0o7777)
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def verify_gate() -> bool:
    """发版前校验门: 镜像与现役一致才放行。"""
    live_sha = sha256_of(LIVE)
    mirror_sha = sha256_of(MIRROR)
    consistent = live_sha == mirror_sha
    print("=== 发版前校验门: build/lib 镜像 vs 现役 ===")
    print(f"  现役 {LIVE}\n    SHA-256: {live_sha}\n    {TARGET} 命中行: "
          f"{count_lines(LIVE)}")
    print(f"  镜像 {MIRROR}\n    SHA-256: {mirror_sha}\n    {TARGET} 命中行: "
          f"{count_lines(MIRROR)}")
    verdict = ("[PASS] 一致 — 允许发版" if consistent
               else "[FAIL] 不一致 — 禁止据此镜像打包(会回退 S4 清理)")
    print(f"\n  判定: {verdict}")
    return consistent


def main() -> int:
    """默认只校验; --apply 才同步镜像。"""
    apply = "--apply" in sys.argv
    if not apply:
        return 0 if verify_gate() else 1

    print("=== 裁定② build/lib 镜像同步 (--apply) ===")
    snap = {p: sha256_of(p) for p in FORBIDDEN}

    print("\n-- 闸1 前置哈希核对 --")
    live_sha = sha256_of(LIVE)
    mirror_sha = sha256_of(MIRROR)
    print(f"  现役实测 {live_sha}\n  现役应为 {LIVE_SHA}")
    print(f"  镜像实测 {mirror_sha}\n  镜像应为 {MIRROR_PRE_SHA}")
    if live_sha != LIVE_SHA or mirror_sha != MIRROR_PRE_SHA:
        print("  [拒改] 前置哈希不符 — 停手上报, 不做任何写入")
        return 2
    print("  [PASS] 现役与镜像均为呈批时版本")

    print("\n-- 闸2 覆盖安全性: 镜像原内容须完整留存于止血基线 --")
    base_sha = sha256_of(BASELINE)
    print(f"  止血基线 SHA {base_sha}")
    print(f"  镜像原内容 == 止血基线: {mirror_sha == base_sha}")
    if mirror_sha != base_sha or base_sha != BASELINE_SHA:
        print("  [拒改] 同源关系不成立, 覆盖将造成内容灭失 — 停手上报")
        return 3
    print("  [PASS] 同源, 覆盖不灭失任何内容(基线受铁律①与 G3 守护)")

    print("\n-- 闸3 待写入内容语义校验 --")
    print(f"  现役 {TARGET} 命中行: {count_lines(LIVE)} (应为 0)")
    print(f"  镜像 {TARGET} 命中行: {count_lines(MIRROR)} (改前, 应为 4)")
    if count_lines(LIVE) != 0 or count_lines(MIRROR) != 4:
        print("  [拒改] 命中数不符预期 — 停手上报")
        return 4
    print("  [PASS]")

    print("\n-- 执行同步(原子写) --")
    atomic_copy(LIVE, MIRROR)
    post_sha = sha256_of(MIRROR)
    print(f"  镜像改前 SHA: {MIRROR_PRE_SHA}")
    print(f"  镜像改后 SHA: {post_sha}")
    print(f"  == 现役 SHA: {post_sha == live_sha}")
    print(f"  镜像 {TARGET} 命中行: {count_lines(MIRROR)} (应为 0)")
    leftover = [f for f in os.listdir(os.path.dirname(MIRROR))
                if f.startswith(".mirror-sync-")]
    print(f"  残留临时文件: {leftover}")

    print("\n-- 全仓 *.py 命中行复核(排除 evidence 工具件) --")
    out = subprocess.run(["grep", "-rn", "--include=*.py", TARGET, "."],
                         cwd=REPO, capture_output=True, text=True)
    in_scope = [ln for ln in out.stdout.splitlines()
                if ln.strip() and "cost_defense/evidence" not in ln.split(":", 1)[0]]
    print(f"  in_scope 命中行: {len(in_scope)} (应为 20 = 24 - 4)")

    print("\n-- 铁律禁改清单收尾核对 --")
    guarded = guard_forbidden(snap)

    print("\n-- 发版前校验门复跑 --")
    gate = verify_gate()

    ok = (post_sha == live_sha and count_lines(MIRROR) == 0
          and not leftover and guarded and gate and len(in_scope) == 20)
    print(f"\n=== 同步总判定: {'全部通过' if ok else '存在异常 — 按铁律停手上报'} ===")
    return 0 if ok else 5


if __name__ == "__main__":
    raise SystemExit(main())
