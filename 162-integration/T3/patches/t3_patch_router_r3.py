#!/usr/bin/env python3
"""162号 P1-A集成 · T3上下文剪枝 · router_r3.py 接线补丁。

接线点（2处）：
    ① T7 import 后：import context_pruning 模块
    ② 新增 /health/pruning 端点：暴露剪枝统计

零破坏：不修改现有消息处理逻辑，仅暴露统计端点。
"""
import os, shutil

ROUTER_PATH = "/home/agentuser/lao-release/lao/effect_anchored/routing/lao_router_server.py"
print(f"[T3-PATCH] Target: {ROUTER_PATH}")
bak = ROUTER_PATH + ".bak.t3"
if not os.path.exists(bak):
    shutil.copy2(ROUTER_PATH, bak)

with open(ROUTER_PATH, "r", encoding="utf-8") as f:
    src = f.read()

changes = 0

# ── ① Import T3 ──────────────
T3_IMPORT = '''
# ── P1-A T3 上下文剪枝（162号集成·2026-09-04） ──
try:
    from context_pruning import ContextPruner, PruningConfig, IntentKeeper
    _T3_ENABLED = True
except ImportError:
    _T3_ENABLED = False
    print("[T3] context_pruning module not found")
'''

if "P1-A T3" not in src:
    anchor = '    print("[T7] task_state_machine module not found")'
    if anchor in src:
        src = src.replace(anchor, anchor + "\n" + T3_IMPORT, 1)
        changes += 1
        print("[T3-PATCH] ① Import T3 — DONE")

# ── ② /health/pruning 端点 ──────────────
T3_ENDPOINT = '''

# ── P1-A T3 剪枝统计端点（162号集成） ──
@app.get("/health/pruning")
async def health_pruning():
    """T3 上下文剪枝健康检查。"""
    return {
        "t3_enabled": _T3_ENABLED,
        "pruning_config": {
            "max_tokens": 8000,
            "protected_roles": ["system", "user"],
        } if _T3_ENABLED else None,
    }
'''

if "/health/pruning" not in src:
    anchor = '@app.get("/health")'
    if anchor in src:
        src = src.replace(anchor, T3_ENDPOINT + "\n" + anchor, 1)
        changes += 1
        print("[T3-PATCH] ② /health/pruning endpoint — DONE")

with open(ROUTER_PATH, "w", encoding="utf-8") as f:
    f.write(src)

print(f"\n[T3-PATCH] Applied {changes} changes")
print("[T3-PATCH] DONE")
