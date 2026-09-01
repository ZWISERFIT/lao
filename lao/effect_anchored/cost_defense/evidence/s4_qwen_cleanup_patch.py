# P0 LAO成本防线: 规格书 _rev2 (SHA-256 4d6bc3aa…17c4) 施工件
"""S4 qwen3.8-max 虚报清理: 现役 model_router.py 四处引用清理(经统筹席核准执行)。

裁定依据(2026-08-31 统筹席):
    ① 本次回归按 R-03 记账, R-04 待 Nova 窗口数据另办;
    ② 清理范围仅限规格 S4-1 所列 4 处; lao_router_server.py L1575-1576 的
       HTTP 峰值硬指派分支、tests 下两处验收价目表 → 列入跟进项, 不擅动;
    ③ 准予摘除 L218 不可达候选(非"标注保留")。

四处改动(与呈批方案逐字一致):
    L167 文档字符串 → 同步移除 qwen3.8-max
    L218 活代码字面量 → 摘除结构性不可达候选
    L719 注释 → 兼修失实示例(实测 qwen3.7-plus→qwen3.6-flash)
    L724 注释 → 兼修失实示例(实测 qwen3.7-plus, 16,588 笔)

安全设计(任何一项不满足即拒绝改动, 原文件分毫不动):
    - 前置哈希必须等于呈批时的现役版本 27b62d45…e2a80;
    - 每处 old 串必须在全文出现且仅出现 1 次;
    - 改动后全文 qwen3.8-max 计数必须为 0;
    - 原子写(临时文件 + fsync + os.replace), 保留原权限位;
    - 只写 model_router.py 本身, 绝不触碰 .bak-hemostasis-* 与 .snapshot-*。

运行:
    cd /home/agentuser/lao-release && python3 lao/effect_anchored/cost_defense/evidence/s4_qwen_cleanup_patch.py
"""

from __future__ import annotations

import hashlib
import os
import tempfile

TARGET = "qwen3.8-max"
ROUTER = "/home/agentuser/lao-release/lao/effect_anchored/routing/model_router.py"
EXPECT_PRE_SHA = "27b62d4532205a16b89d3ba270d3cdc55403c53f056e828d2b0db447e84e2a80"
SNAPSHOT = ROUTER + ".snapshot-pre-s4-20260831"
BASELINE = ROUTER + ".bak-hemostasis-20260830"

# (定位描述, 原文串, 新文串) —— 原文串均取自远端实测行, 不含缩进以免空白差异误伤
EDITS = (
    (
        "L167 文档字符串",
        "token-plan(qwen3.6-flash/qwen3.7-plus/qwen3.8-max/glm-5.2)",
        "token-plan(qwen3.6-flash/qwen3.7-plus/glm-5.2)",
    ),
    (
        "L218 活代码字面量",
        'for mid in ("qwen3.6-flash", "qwen3.7-plus", "qwen3.8-max", "glm-5.2"):',
        'for mid in ("qwen3.6-flash", "qwen3.7-plus", "glm-5.2"):',
    ),
    (
        "L719 注释",
        "# 方案A: 同 provider flash 档降级(如 qwen3.8-max→qwen3.6-flash)",
        "# 方案A: 同 provider flash 档降级(实测: qwen3.7-plus→qwen3.6-flash)",
    ),
    (
        "L724 注释",
        "# 审计 from = 被降级 provider 首个非 flash 档(PRD例: qwen3.8-max)",
        "# 审计 from = 被降级 provider 首个非 flash 档(实测: qwen3.7-plus, 16,588笔)",
    ),
)


def sha256_of(path: str) -> str:
    """文件 SHA-256。"""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write(path: str, text: str) -> None:
    """原子写: 同目录临时文件 + fsync + os.replace, 并保留原权限位。"""
    directory = os.path.dirname(path)
    mode = os.stat(path).st_mode
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-s4-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode & 0o7777)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main() -> int:
    """执行四处清理; 任何前置条件不满足即拒绝改动并返回非零。"""
    print("=== S4 qwen3.8-max 清理施工 (经统筹席核准) ===")
    print(f"目标文件: {ROUTER}")

    pre_sha = sha256_of(ROUTER)
    print(f"前置 SHA-256: {pre_sha}")
    if pre_sha != EXPECT_PRE_SHA:
        print(f"拒绝改动: 前置哈希与呈批版本不符(应为 {EXPECT_PRE_SHA})")
        return 1
    print("  前置哈希核对: 通过(与呈批时现役版本一致)")

    for guard, label in ((SNAPSHOT, "快照"), (BASELINE, "止血基线")):
        print(f"  {label}存在性: {os.path.isfile(guard)} ({os.path.basename(guard)}) — 本脚本不写此文件")

    with open(ROUTER, "r", encoding="utf-8", newline="") as fh:
        text = fh.read()

    before_count = text.count(TARGET)
    print(f"\n改动前全文 {TARGET} 计数: {before_count}")

    # 逐处唯一性断言(全部通过后才落盘)
    for where, old, _new in EDITS:
        hits = text.count(old)
        print(f"  {where}: 原文串出现 {hits} 次")
        if hits != 1:
            print(f"拒绝改动: {where} 原文串须恰好出现 1 次, 实际 {hits} 次")
            return 1

    for where, old, new in EDITS:
        text = text.replace(old, new, 1)
        print(f"  已应用: {where}")

    after_count = text.count(TARGET)
    if after_count != 0:
        print(f"拒绝落盘: 改动后仍残留 {TARGET} {after_count} 处")
        return 1

    _atomic_write(ROUTER, text)
    post_sha = sha256_of(ROUTER)
    leftovers = [n for n in os.listdir(os.path.dirname(ROUTER)) if n.startswith(".tmp-s4-")]
    print(f"\n落盘完成。后置 SHA-256: {post_sha}")
    print(f"改动后全文 {TARGET} 计数: {after_count}")
    print(f"残留临时文件: {leftovers}")
    print(f"快照仍为: {sha256_of(SNAPSHOT)}")
    print(f"止血基线仍为: {sha256_of(BASELINE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
