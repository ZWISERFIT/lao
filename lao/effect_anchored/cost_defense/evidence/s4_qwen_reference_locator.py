# P0 LAO成本防线: 规格书 _rev2 (SHA-256 4d6bc3aa…17c4) 证据件
"""S4 第一步: qwen3.8-max 引用定位器(只读, 不改现役路由一个字节)。

规格锚点: S4-1 清理范围(现役 model_router.py 4 处引用 L167/L218/L719/L724)、
         S4-2 清理原则(先快照归档再改; 清理后 grep 归零)。

本脚本只做三件事:
    1. 逐处定位 qwen3.8-max 引用, 并用 ast+tokenize 判定每处属于
       注释 / 文档字符串 / **活代码字面量** —— 决定清理是否改变选路行为。
    2. 只读探测 openclaw.json 的 token-plan 模型声明, 判定 L218 候选项
       当前是否真进入 MODEL_POOL(进入才构成行为变更)。
    3. 只读探测两账本内 qwen3.8-max 的虚报数据面, 输出计数与指纹。

纪律: 全程 open 模式 "r"/"rb"; 不写任何文件; 不 import subprocess/网络库。

运行:
    cd /home/agentuser/lao-release && python3 lao/effect_anchored/cost_defense/evidence/s4_qwen_reference_locator.py
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import tokenize

TARGET = "qwen3.8-max"
ROUTER = "/home/agentuser/lao-release/lao/effect_anchored/routing/model_router.py"
BASELINE = ROUTER + ".bak-hemostasis-20260830"
CONFIG = "/home/agentuser/.openclaw/openclaw.json"
COST_LOG = "/home/agentuser/lao-release/lao/routing_cost_log.json"
AUDIT = "/home/agentuser/lao-release/lao/switch_audit.jsonl"

KIND_COMMENT = "注释"
KIND_DOCSTRING = "文档字符串"
KIND_LIVE = "活代码字面量"


def sha256_of(path: str) -> str:
    """文件 SHA-256(证据三件套之一)。"""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _docstring_lines(source: str) -> set:
    """收集模块/类/函数文档字符串占用的行号集合。"""
    lines: set = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return lines
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if not isinstance(first, ast.Expr) or not isinstance(first.value, ast.Constant):
            continue
        if not isinstance(first.value.value, str):
            continue
        for ln in range(first.lineno, (first.end_lineno or first.lineno) + 1):
            lines.add(ln)
    return lines


def _comment_lines(source: str) -> set:
    """收集注释 token 占用的行号集合。"""
    lines: set = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                lines.add(tok.start[0])
    except (tokenize.TokenError, IndentationError):
        pass
    return lines


def locate_references(path: str) -> list:
    """逐处定位并归类; 返回 [(行号, 归类, 行原文)]。"""
    with open(path, "r", encoding="utf-8") as fh:
        source = fh.read()
    docs = _docstring_lines(source)
    comments = _comment_lines(source)
    hits = []
    for idx, line in enumerate(source.splitlines(), start=1):
        if TARGET not in line:
            continue
        if idx in comments:
            kind = KIND_COMMENT
        elif idx in docs:
            kind = KIND_DOCSTRING
        else:
            kind = KIND_LIVE
        hits.append((idx, kind, line.strip()))
    return hits


def probe_config(path: str) -> dict:
    """只读探测 openclaw.json: token-plan 是否声明 TARGET(决定 L218 是否活候选)。"""
    out = {"exists": os.path.isfile(path), "token_plan_ids": [], "declared": False,
           "agent_declarations": [], "raw_hits": 0}
    if not out["exists"]:
        return out
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    out["raw_hits"] = text.count(TARGET)
    try:
        cfg = json.loads(text)
    except ValueError:
        return out
    providers = (cfg.get("models") or {}).get("providers") or {}
    tp = providers.get("token-plan") or {}
    ids = [m.get("id", "") for m in (tp.get("models") or []) if m.get("id")]
    out["token_plan_ids"] = ids
    out["declared"] = TARGET in ids
    for agent in ((cfg.get("agents") or {}).get("list") or []):
        md = agent.get("model") or {}
        refs = [md.get("primary", "")] + list(md.get("fallbacks") or [])
        if any(TARGET in str(r) for r in refs):
            out["agent_declarations"].append(agent.get("id", ""))
    return out


def probe_cost_log(path: str) -> dict:
    """只读探测 routing_cost_log.json: 含 TARGET 的成本记录(虚报数据面)。"""
    out = {"exists": os.path.isfile(path), "total": 0, "hits": 0, "hit_cost_usd": 0.0,
           "hit_models": {}}
    if not out["exists"]:
        return out
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    records = payload.get("records") if isinstance(payload, dict) else payload
    records = records if isinstance(records, list) else []
    out["total"] = len(records)
    for rec in records:
        if not isinstance(rec, dict):
            continue
        model = str(rec.get("model", "") or "")
        if TARGET not in model:
            continue
        out["hits"] += 1
        out["hit_models"][model] = out["hit_models"].get(model, 0) + 1
        try:
            out["hit_cost_usd"] += float(rec.get("cost_usd", 0.0) or 0.0)
        except (TypeError, ValueError):
            pass
    return out


def probe_audit(path: str) -> dict:
    """只读探测 switch_audit.jsonl: TARGET 出现在 from_model/to_model 的笔数。"""
    out = {"exists": os.path.isfile(path), "lines": 0, "from_hits": 0, "to_hits": 0}
    if not out["exists"]:
        return out
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            out["lines"] += 1
            if TARGET not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if TARGET in str(rec.get("from_model", "")):
                out["from_hits"] += 1
            if TARGET in str(rec.get("to_model", "")):
                out["to_hits"] += 1
    return out


def main() -> int:
    """输出 S4 第一步定位证据(只读)。"""
    print("=== S4 qwen3.8-max 引用定位 (只读·未改动任何文件) ===")
    print(f"目标串: {TARGET}")
    print(f"\n[现役路由器] {ROUTER}")
    print(f"  SHA-256: {sha256_of(ROUTER)}")
    print(f"[止血基线] {BASELINE}")
    print(f"  SHA-256: {sha256_of(BASELINE)}  (只读不动)")

    hits = locate_references(ROUTER)
    print(f"\n[逐处定位] 共 {len(hits)} 处")
    live = 0
    for lineno, kind, text in hits:
        if kind == KIND_LIVE:
            live += 1
        print(f"  L{lineno} [{kind}] {text}")
    print(f"  归类小计: 活代码 {live} 处 / 非活代码 {len(hits) - live} 处")

    cfg = probe_config(CONFIG)
    print(f"\n[声明源探测] {CONFIG}")
    print(f"  全文出现次数: {cfg['raw_hits']}")
    print(f"  token-plan 声明模型 id: {cfg['token_plan_ids']}")
    print(f"  token-plan 是否声明 {TARGET}: {cfg['declared']}")
    print(f"  agent 绑定引用 {TARGET} 的 agent: {cfg['agent_declarations']}")
    print(f"  → L218 候选项当前是否真进入 MODEL_POOL: {cfg['declared']}")

    cost = probe_cost_log(COST_LOG)
    print(f"\n[成本账本探测] {COST_LOG}")
    print(f"  records 总数: {cost['total']}; 命中 {TARGET} 的记录: {cost['hits']} 条")
    print(f"  命中记录成本合计: {round(cost['hit_cost_usd'], 6)} USD; 型号分布: {cost['hit_models']}")

    audit = probe_audit(AUDIT)
    print(f"\n[审计账本探测] {AUDIT}")
    print(f"  总行数: {audit['lines']}; from_model 命中: {audit['from_hits']} 笔;"
          f" to_model 命中: {audit['to_hits']} 笔")

    print("\n结论要点:")
    print(f"  1. 4 处引用中活代码 {live} 处 —— 只有活代码处的改动会影响选路。")
    print(f"  2. token-plan 声明 {TARGET} = {cfg['declared']}; 决定 L218 清理是否为行为变更。")
    print(f"  3. 成本账本虚报面 = {cost['hits']} 条; 审计账本 = "
          f"{audit['from_hits'] + audit['to_hits']} 笔提及。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
