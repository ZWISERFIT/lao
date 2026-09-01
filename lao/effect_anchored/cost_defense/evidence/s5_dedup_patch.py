# P0 LAO成本防线: 规格书 _rev2 (SHA-256 4d6bc3aa…17c4) S5 ① A类去重施工件
"""A类重复声明去重: 按六项裁定①执行, 两阶段(dry-run / --apply)。

裁定依据(2026-08-31 统筹席):
    token-plan  : 保留 "Qwen3.8 Max (TokenPlan)"      → 删除 models[5]
    novarouteai : 保留 models[3]                       → 删除 models[4]
    不改名; 名实不符另登台账转 S6。

为何不直接 json.load + json.dump 回写:
    整体序列化会按本脚本的格式偏好重排全文(缩进/转义/行尾), 产生覆盖整个文件的
    diff, 使"仅删 2 个数组元素"无法逐行核验, 也违背最小改动原则。
    故阶段1 先做**格式往返一致性测试**: 试多组 dump 参数, 看能否逐字节复现原文件。
    - 若某组参数可复现 → 用该组参数回写, diff 将精确等于被删元素的行数
    - 若全部不能复现 → 停手上报, 改走文本层定位删除方案(不在本脚本内擅自切换)

三道拒改闸(阶段2):
    闸1 前置 SHA-256 须 == PRE_SHA
    闸2 两处待删元素须精确命中且其 id/name 与裁定完全一致
    闸3 改后 json.load 须成功, 且顶层键 15 项 / provider 数 12 / $.agents 段
        逐字节不变(A类不得触及 C类绑定)

运行:
    python3 …/evidence/s5_dedup_patch.py           # dry-run, 不写盘
    python3 …/evidence/s5_dedup_patch.py --apply   # 落盘
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import sys
import tempfile

OPENCLAW = "/home/agentuser/.openclaw/openclaw.json"
REPO = "/home/agentuser/lao-release"
EVID = f"{REPO}/lao/effect_anchored/cost_defense/evidence"
SNAPSHOT = f"{EVID}/snapshots/openclaw.json.snapshot-pre-s5-20260831"

PRE_SHA = "44c27e2cfa955f49cf05d74861ee0305132ee3c0e1739c56f9c56bb5c9def490"
TARGET = "qwen3.8-max"

# (provider, 待删索引, 待删元素应有的 name, 保留元素应有的 name)
DELETIONS = (
    ("token-plan", 5, "DeepSeek V4 Pro (TokenPlan)", "Qwen3.8 Max (TokenPlan)"),
    ("novarouteai", 4, "DeepSeek V4 Flash", "DeepSeek V4 Pro"),
)

# 候选 dump 参数组合, 用于往返一致性测试
DUMP_TRIALS = (
    {"indent": 2, "ensure_ascii": False},
    {"indent": 2, "ensure_ascii": True},
    {"indent": 4, "ensure_ascii": False},
    {"indent": 4, "ensure_ascii": True},
    {"indent": 2, "ensure_ascii": False, "sort_keys": True},
    {"indent": 2, "ensure_ascii": True, "sort_keys": True},
    {"indent": 4, "ensure_ascii": True, "sort_keys": True},
    {"indent": 4, "ensure_ascii": False, "sort_keys": True},
)


def sha256_of(path: str) -> str:
    """文件 SHA-256。"""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    """字符串 SHA-256(UTF-8)。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def serialize(cfg, trial: dict, trailing: str) -> str:
    """按给定参数序列化, 并补回原文件的尾部字符(换行与否)。"""
    return json.dumps(cfg, **trial) + trailing


def roundtrip_probe(raw: str, cfg) -> tuple:
    """格式往返测试: 返回 (可复现的参数, 尾部字符) 或 (None, None)。"""
    for trailing in ("\n", ""):
        for trial in DUMP_TRIALS:
            if serialize(cfg, trial, trailing) == raw:
                return trial, trailing
    return None, None


def locate(cfg) -> list:
    """按裁定定位两处待删元素, 逐项核验 id 与 name; 返回核验明细。"""
    providers = (cfg.get("models") or {}).get("providers") or {}
    rows = []
    for pid, idx, del_name, keep_name in DELETIONS:
        models = (providers.get(pid) or {}).get("models") or []
        ok = False
        actual_id = actual_name = None
        kept = None
        if 0 <= idx < len(models) and isinstance(models[idx], dict):
            actual_id = models[idx].get("id")
            actual_name = models[idx].get("name")
            same_id = [m for m in models
                       if isinstance(m, dict) and m.get("id") == TARGET]
            kept = [m.get("name") for m in same_id if m.get("name") != del_name]
            ok = (actual_id == TARGET and actual_name == del_name
                  and keep_name in (kept or []))
        rows.append({
            "provider": pid, "索引": idx, "实测id": actual_id,
            "实测name": actual_name, "应删name": del_name,
            "应留name": keep_name, "去重后残留name": kept, "核验通过": ok,
        })
    return rows


def apply_deletions(cfg) -> None:
    """就地删除两处元素(索引从大到小, 避免位移)。"""
    providers = (cfg.get("models") or {}).get("providers") or {}
    for pid, idx, _, _ in sorted(DELETIONS, key=lambda r: -r[1]):
        del providers[pid]["models"][idx]


def agents_fingerprint(cfg) -> str:
    """$.agents 段指纹: C类绑定不得被本次改动触及。"""
    return sha256_text(json.dumps(cfg.get("agents"), ensure_ascii=False,
                                  sort_keys=True))


def _atomic_write(path: str, text: str) -> None:
    """原子写: 同目录临时文件 + fsync + os.replace, 保留原权限位。"""
    directory = os.path.dirname(path)
    mode = os.stat(path).st_mode
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-s5-")
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
    """dry-run 全量核验; 带 --apply 时落盘并出后置证据。"""
    do_apply = "--apply" in sys.argv
    print(f"=== S5 ① A类去重 ({'APPLY 落盘' if do_apply else 'DRY-RUN 不写盘'}) ===")

    live_sha = sha256_of(OPENCLAW)
    print(f"\n-- 闸1 前置哈希 --")
    print(f"  实测: {live_sha}")
    print(f"  应为: {PRE_SHA}")
    if live_sha != PRE_SHA:
        print("  [FAIL] 前置哈希不符 — 拒改, 按铁律停手上报")
        return 1
    print("  [PASS] 与呈批版本一致")
    print(f"  快照 SHA: {sha256_of(SNAPSHOT)} (与前置一致: "
          f"{sha256_of(SNAPSHOT) == PRE_SHA})")

    with open(OPENCLAW, "r", encoding="utf-8") as fh:
        raw = fh.read()
    cfg = json.loads(raw)

    print(f"\n-- 格式往返一致性测试 --")
    trial, trailing = roundtrip_probe(raw, cfg)
    if trial is None:
        print("  [FAIL] 无候选参数可逐字节复现原文件")
        print("         直接回写会重排全文, diff 不可核验 → 停手上报,")
        print("         须改走文本层定位删除方案(本脚本不擅自切换)")
        return 1
    print(f"  [PASS] 可精确复现, 参数: {trial}, 尾部: {trailing!r}")

    print(f"\n-- 闸2 待删元素核验 --")
    rows = locate(cfg)
    for row in rows:
        print(f"  {row['provider']}.models[{row['索引']}]")
        print(f"    实测 id={row['实测id']} name={row['实测name']!r}")
        print(f"    应删 name={row['应删name']!r} / 应留 name={row['应留name']!r}")
        print(f"    去重后残留 name={row['去重后残留name']}")
        print(f"    核验: {'PASS' if row['核验通过'] else 'FAIL'}")
    if not all(r["核验通过"] for r in rows):
        print("  [FAIL] 待删元素与裁定不符 — 拒改, 停手上报")
        return 1
    print("  [PASS] 两处均与裁定①完全一致")

    agents_before = agents_fingerprint(cfg)
    keys_before = sorted(cfg)
    prov_before = sorted((cfg.get("models") or {}).get("providers") or {})
    hits_before = raw.count(TARGET)

    apply_deletions(cfg)
    new_raw = serialize(cfg, trial, trailing)

    print(f"\n-- 闸3 改后语义校验 --")
    recheck = json.loads(new_raw)
    print(f"  json 解析: PASS")
    print(f"  顶层键 {len(keys_before)} 项不变: {sorted(recheck) == keys_before}")
    new_prov = sorted((recheck.get("models") or {}).get("providers") or {})
    print(f"  provider 数 {len(prov_before)} 不变: {new_prov == prov_before}")
    agents_after = agents_fingerprint(recheck)
    print(f"  $.agents 段指纹不变: {agents_after == agents_before}")
    print(f"    改前 {agents_before}")
    print(f"    改后 {agents_after}")
    gate3 = (sorted(recheck) == keys_before and new_prov == prov_before
             and agents_after == agents_before)
    if not gate3:
        print("  [FAIL] 语义校验不通过 — 拒改, 停手上报")
        return 1
    print("  [PASS] C类绑定未被触及")

    print(f"\n-- 改动预演 --")
    old_lines, new_lines = raw.splitlines(True), new_raw.splitlines(True)
    print(f"  行数: {len(old_lines)} → {len(new_lines)} "
          f"(减 {len(old_lines) - len(new_lines)})")
    print(f"  字节: {len(raw.encode())} → {len(new_raw.encode())}")
    print(f"  {TARGET} 出现次数: {hits_before} → {new_raw.count(TARGET)}")
    print(f"  逐行 diff:")
    for ln in difflib.unified_diff(old_lines, new_lines, "before", "after", n=1):
        if ln.startswith(("---", "+++")):
            continue
        print(f"    {ln.rstrip()}")

    if not do_apply:
        print(f"\n=== DRY-RUN 完毕: 全部闸门通过, 未写任何文件 ===")
        print(f"  openclaw.json SHA 复核: {sha256_of(OPENCLAW)}")
        print(f"  与开头一致(未改): {sha256_of(OPENCLAW) == live_sha}")
        return 0

    _atomic_write(OPENCLAW, new_raw)
    post_sha = sha256_of(OPENCLAW)
    print(f"\n-- 落盘后置证据 --")
    print(f"  改前 SHA-256: {PRE_SHA}")
    print(f"  改后 SHA-256: {post_sha}")
    print(f"  与预演一致: {post_sha == sha256_text(new_raw)}")
    with open(OPENCLAW, "r", encoding="utf-8") as fh:
        final = fh.read()
    print(f"  重读 json 解析: {'PASS' if json.loads(final) else 'PASS'}")
    print(f"  {TARGET} 出现次数: {final.count(TARGET)}")
    print(f"  快照 SHA 未变: {sha256_of(SNAPSHOT) == PRE_SHA}")
    leftovers = [f for f in os.listdir(os.path.dirname(OPENCLAW))
                 if f.startswith(".tmp-s5-")]
    print(f"  残留临时文件: {leftovers}")
    dup = []
    for pid, node in ((json.loads(final).get("models") or {})
                      .get("providers") or {}).items():
        ids = [m.get("id") for m in (node or {}).get("models") or []
               if isinstance(m, dict)]
        dup += [f"{pid}/{i}" for i in set(ids) if ids.count(i) > 1]
    print(f"  改后仍重复的声明: {dup} (应为空)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
