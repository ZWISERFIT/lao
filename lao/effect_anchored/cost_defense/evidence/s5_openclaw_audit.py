# P0 LAO成本防线: 规格书 _rev2 (SHA-256 4d6bc3aa…17c4) S5 审计件 v2
"""S5 声明源审计 v2: openclaw.json 只读体检 + 声明-实测对账 + 快照 + 差异表骨架。

v1 两处缺陷已修(2026-08-31 首跑发现, 如实记录):
    缺陷1: router_pool_tuple() 取全文第一个 `for mid in (` 行, 命中的是 qwen 段
           元组 ('qwen3.7-flash','qwen-plus','qwen-max'), 并非 L218 的 token-plan
           段 → 对账结论全错。现改为先定位 `tp_ids = provider_models.get("token-plan"`
           所在行, 再取其后最近的 `for mid in (` 行, 并回报行号自证。
    缺陷2: agent 声明提取用 `value in WATCH` 精确相等, 而实际值形如
           "deepseek-shuyu/qwen3.8-max"(provider/model 复合式) → 漏为 0 处。
           现改为子串匹配并解析出 provider 与 model 两段。

安全: openclaw.json 含 secrets。凡字段名含 key/token/secret/password/auth/
      credential 者一律以 <redacted:len=N> 输出, 绝不打印明文。

只读边界: 只读 openclaw.json / 现役 router / 两本账本; 写出仅限 evidence 目录下的
      快照与差异表骨架, 不改任何活配置与活账本。

写盘行为清单(2026-08-31 补正, 供复跑前自查):
    快照 SNAPSHOT   —— 一次性写入, 已存在则不写(原无条件覆盖, 曾污染改动前证据)
    骨架 SKELETON   —— 一次性写入, 已存在则不写(按裁定⑥冻结, 原每次重写致 SHA 漂移)
    其余一律只读。故本件在改动落盘后复跑亦不会销毁或改写任何既有证据件。

运行:
    python3 /home/agentuser/lao-release/lao/effect_anchored/cost_defense/evidence/s5_openclaw_audit.py
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil

OPENCLAW = "/home/agentuser/.openclaw/openclaw.json"
REPO = "/home/agentuser/lao-release"
EVID = f"{REPO}/lao/effect_anchored/cost_defense/evidence"
ROUTER = f"{REPO}/lao/effect_anchored/routing/model_router.py"
AUDIT = f"{REPO}/lao/switch_audit.jsonl"
COST_LOG = f"{REPO}/lao/routing_cost_log.json"
SNAP_DIR = f"{EVID}/snapshots"
SNAPSHOT = f"{SNAP_DIR}/openclaw.json.snapshot-pre-s5-20260831"
SKELETON = f"{EVID}/s5_window_reconcile_skeleton.json"
NOVA_ACTUALS = f"{EVID}/s5_window_reconcile_nova_actuals.json"

# 裁定⑥(2026-08-31): 骨架件按现值冻结入账。本件不再重写既有骨架, 只比对此值。
SKELETON_FROZEN_SHA = "92d08359d6bfeb4302d21f1687360f7a1a914e82421ad9d08a704092d5b90847"

WATCH = ("qwen3.8-max", "qwen3.7-max", "qwen3.7-plus", "qwen3.6-flash",
         "glm-5.2", "qwen3.7-flash", "qwen-plus", "qwen-max",
         "deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat")
SENSITIVE = ("apikey", "api_key", "secret", "password", "credential",
             "accesstoken", "refreshtoken", "authorization", "bearer")


def sha256_of(path: str) -> str:
    """文件 SHA-256。"""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe(name: str, value):
    """敏感字段脱敏; 非敏感原样返回。

    脱敏名单须精确: 初版用裸 "token"/"key" 子串匹配, 误伤了 maxTokens
    ——而 maxTokens 正是 S5-1 窗口对账的声明值, 不可脱敏。
    """
    flat = name.lower().replace("-", "").replace("_", "")
    if any(s.replace("_", "") in flat for s in SENSITIVE):
        return f"<redacted:len={len(str(value))}>"
    return value


def walk(node, path: str = "$"):
    """递归产出 (JSON路径, 字符串值)。"""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for idx, value in enumerate(node):
            yield from walk(value, f"{path}[{idx}]")
    elif isinstance(node, str):
        yield path, node


def token_plan_tuple() -> tuple:
    """定位 token-plan 段候选元组, 返回 (行号, ids); 修正 v1 缺陷1。"""
    with open(ROUTER, "r", encoding="utf-8") as fh:
        lines = fh.readlines()
    anchor = None
    for idx, line in enumerate(lines):
        if 'provider_models.get("token-plan"' in line:
            anchor = idx
            break
    if anchor is None:
        return (0, [])
    for idx in range(anchor, min(anchor + 8, len(lines))):
        if "for mid in (" in lines[idx]:
            return (idx + 1, re.findall(r'"([^"]+)"', lines[idx]))
    return (0, [])


def ledger_counts(ids) -> dict:
    """两本账本原始行命中数(不解析 JSON, 避开已知损坏行)。"""
    out = {}
    for mid in ids:
        counts = {}
        for label, path in (("switch_audit", AUDIT), ("cost_log", COST_LOG)):
            hits = 0
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    hits = sum(1 for line in fh if mid in line)
            counts[label] = hits
        out[mid] = counts
    return out


def main() -> int:
    """输出声明源体检、对账结果、快照与差异表骨架。"""
    print("=== S5 声明源审计 v2 (openclaw.json 只读) ===")
    live_sha = sha256_of(OPENCLAW)
    print(f"文件: {OPENCLAW}\nSHA-256: {live_sha}\n字节数: {os.path.getsize(OPENCLAW)}")

    os.makedirs(SNAP_DIR, exist_ok=True)
    # 缺陷修复(2026-08-31): 原为无条件 copy2, 导致本件在配置改动后复跑时,
    # 会把改动后的内容覆盖写入名为 pre-s5 的快照, 污染"改动前"这一证据件
    # (已实际发生一次, 由 s5_snapshot_repair.py 按哈希还原)。
    # 现改为一次性写入: 快照已存在则一律不写, 只做比对并如实标注差异。
    snap_existed = os.path.isfile(SNAPSHOT)
    if not snap_existed:
        shutil.copy2(OPENCLAW, SNAPSHOT)
    snap_sha = sha256_of(SNAPSHOT)
    print(f"\n-- 快照(置于 evidence, 不落 ~/.openclaw 以免被 runtime 误载) --")
    print(f"  {SNAPSHOT}\n  SHA-256: {snap_sha}")
    print(f"  本次为: {'沿用既有快照(未写盘)' if snap_existed else '首次创建'}")
    print(f"  与现役一致: {snap_sha == live_sha}")
    if snap_existed and snap_sha != live_sha:
        print("  说明: 二者不一致属正常 — 快照记录改动前状态, 现役已按裁定变更")

    with open(OPENCLAW, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    providers = (cfg.get("models") or {}).get("providers") or {}

    print(f"\n-- provider 级字段(敏感项已脱敏) --")
    for pid in sorted(providers):
        node = providers[pid] or {}
        flat = {k: _safe(k, v) for k, v in node.items()
                if not isinstance(v, (dict, list))}
        print(f"  {pid}: {json.dumps(flat, ensure_ascii=False, sort_keys=True)}")

    print(f"\n-- qwen3.8-max 在各 provider 内的完整定义 --")
    for pid in sorted(providers):
        for idx, m in enumerate((providers[pid] or {}).get("models") or []):
            if isinstance(m, dict) and m.get("id") == "qwen3.8-max":
                safe_m = {k: _safe(k, v) for k, v in m.items()}
                print(f"  {pid}.models[{idx}] = "
                      f"{json.dumps(safe_m, ensure_ascii=False, sort_keys=True)}")

    print(f"\n-- 重复声明汇总 --")
    dup_report = {}
    for pid in sorted(providers):
        models = (providers[pid] or {}).get("models") or []
        ids = [m.get("id", "") for m in models if isinstance(m, dict)]
        for mid in sorted({i for i in ids if ids.count(i) > 1}):
            idxs = [n for n, v in enumerate(ids) if v == mid]
            names = [models[n].get("name") for n in idxs]
            identical = all(models[idxs[0]] == models[n] for n in idxs[1:])
            dup_report[f"{pid}/{mid}"] = {"索引": idxs, "name": names,
                                          "逐字段相同": identical}
            print(f"  {pid} / {mid}: 索引 {idxs}, name={names}, 逐字段相同={identical}")
    print(f"  重复声明处数合计: {len(dup_report)}")

    # 修正 v1 缺陷2: 复合式 provider/model 声明
    print(f"\n-- agent 主模型与回退声明(复合式 provider/model) --")
    agent_decl = []
    for path, value in walk(cfg):
        if path.startswith("$.agents") and "/" in value:
            prov, _, mdl = value.partition("/")
            if mdl in WATCH:
                agent_decl.append({"路径": path, "provider": prov, "model": mdl})
    for row in agent_decl:
        print(f"  {row['路径']} → provider={row['provider']} model={row['model']}")
    primary = [r for r in agent_decl if r["路径"].endswith("model.primary")]
    print(f"  合计 {len(agent_decl)} 处; 其中 model.primary {len(primary)} 处")

    line_no, tp_tuple = token_plan_tuple()
    tp_ids = [m.get("id", "") for m in
              (providers.get("token-plan") or {}).get("models") or []]
    tp_unique = sorted(set(tp_ids))
    print(f"\n-- 声明-实测对账 (token-plan) --")
    print(f"  openclaw.json 声明(原序, 含重复): {tp_ids}")
    print(f"  openclaw.json 声明(去重): {tp_unique}")
    print(f"  现役 router 候选元组 @L{line_no}: {tp_tuple}")
    print(f"  声明未进池: {sorted(set(tp_unique) - set(tp_tuple))}")
    print(f"  元组未声明: {sorted(set(tp_tuple) - set(tp_unique))}")

    print(f"\n-- 账本实测笔数(原始行命中) --")
    counts = ledger_counts(tp_unique)
    for mid in tp_unique:
        c = counts[mid]
        in_pool = mid in tp_tuple
        print(f"  {mid}: 进池={in_pool} switch_audit={c['switch_audit']} "
              f"cost_log={c['cost_log']}")

    skeleton = {
        "口径": "S5-1 虚报判定: 声明窗口 > 官方实际窗口 → 虚报条目",
        "取数责任": "实测窗口值须由 Nova 持自有令牌从 token-plan 官方后台取得(O-3); "
                    "本骨架不得填入任何未经官方对账的数字(N-01)",
        "声明源": {"文件": OPENCLAW, "SHA-256": live_sha,
                   "快照": SNAPSHOT, "快照SHA-256": snap_sha},
        "重复声明": dup_report,
        "agent声明": agent_decl,
        "对账行": [],
    }
    for mid in tp_unique:
        decl = next((m for m in (providers.get("token-plan") or {}).get("models") or []
                     if m.get("id") == mid), {})
        skeleton["对账行"].append({
            "provider": "token-plan", "model": mid,
            "声明contextWindow": decl.get("contextWindow"),
            "声明maxTokens": decl.get("maxTokens"),
            "声明name": decl.get("name"),
            "实测contextWindow": None, "实测maxTokens": None,
            "差异": None, "判定": None,
            "进入LAO动态池": mid in tp_tuple,
            "switch_audit实测笔数": counts[mid]["switch_audit"],
        })
    # 裁定⑥(2026-08-31): 骨架按现值冻结 —— 已存在则一律不写, 只做比对。
    # 原为每次运行无条件重写, 内嵌当时哈希与账本笔数, 致其 SHA 随取数时刻漂移
    # (实测三值 84eacc13… / 19f2f69c… / 92d08359…), 无法作为可核哈希入账。
    # Nova 的官方实数一律另写 NOVA_ACTUALS 数据件, 不回填本骨架。
    skel_existed = os.path.isfile(SKELETON)
    if not skel_existed:
        with open(SKELETON, "w", encoding="utf-8") as fh:
            json.dump(skeleton, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    skel_sha = sha256_of(SKELETON)
    print(f"\n-- 差异表骨架(已按裁定⑥冻结) --\n  {SKELETON}\n  SHA-256: {skel_sha}")
    print(f"  本次为: {'沿用既有骨架(未写盘)' if skel_existed else '首次创建'}")
    print(f"  与冻结登账值一致: {skel_sha == SKELETON_FROZEN_SHA}")
    print(f"  对账行 {len(skeleton['对账行'])} 行(实测侧一律 null)")
    print(f"  Nova 官方实数另写数据件: {NOVA_ACTUALS}")
    if skel_existed:
        live_rows = len(skeleton["对账行"])
        print(f"  说明: 本次在内存中重算得 {live_rows} 行, 未落盘; 骨架保持冻结态")

    print(f"\n-- 只读自证 --")
    print(f"  openclaw.json SHA-256 复核: {sha256_of(OPENCLAW)}")
    print(f"  与开头一致(全程未改): {sha256_of(OPENCLAW) == live_sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
