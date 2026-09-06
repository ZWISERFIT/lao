#!/usr/bin/env python3
"""162号 P1-A集成 · T9评测集 · router_r3.py 接线补丁。

接线点（2处）：
    ① T6 import 后：import evaluation_suite 模块
    ② /health/evaluation 端点：暴露评测状态

零破坏：不修改现有路由逻辑，仅暴露评测端点。
"""
import os, shutil

ROUTER_PATH = "/home/agentuser/lao-release/lao/effect_anchored/routing/lao_router_server.py"
print(f"[T9-PATCH] Target: {ROUTER_PATH}")
bak = ROUTER_PATH + ".bak.t9"
if not os.path.exists(bak):
    shutil.copy2(ROUTER_PATH, bak)

with open(ROUTER_PATH, "r", encoding="utf-8") as f:
    src = f.read()

changes = 0

# ── ① Import T9 ──────────────
T9_IMPORT = '''
# ── P1-A T9 匿名评测集（162号集成·2026-09-04） ──
try:
    from evaluation_suite import (
        ABEngine, EvalDataset, DEFAULT_EVAL_DATASET, EvalReport
    )
    _T9_ENABLED = True
except ImportError:
    _T9_ENABLED = False
    print("[T9] evaluation_suite module not found")
'''

if "P1-A T9" not in src:
    anchor = '    print("[T6] cost_reconciliation module not found")'
    if anchor in src:
        src = src.replace(anchor, anchor + "\n" + T9_IMPORT, 1)
        changes += 1
        print("[T9-PATCH] ① Import T9 — DONE")

# ── ② /health/evaluation 端点 ──────────────
T9_ENDPOINT = '''

# ── P1-A T9 评测状态端点（162号集成） ──
@app.get("/health/evaluation")
async def health_evaluation():
    """T9 匿名评测集健康检查。"""
    return {
        "t9_enabled": _T9_ENABLED,
        "dataset_size": len(DEFAULT_EVAL_DATASET.items) if _T9_ENABLED else 0,
        "synthetic_only": True,
    }
'''

if "/health/evaluation" not in src:
    anchor = '@app.get("/health")'
    if anchor in src:
        src = src.replace(anchor, T9_ENDPOINT + "\n" + anchor, 1)
        changes += 1
        print("[T9-PATCH] ② /health/evaluation endpoint — DONE")

with open(ROUTER_PATH, "w", encoding="utf-8") as f:
    f.write(src)

print(f"\n[T9-PATCH] Applied {changes} changes")
print("[T9-PATCH] DONE")
