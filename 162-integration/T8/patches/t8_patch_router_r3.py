#!/usr/bin/env python3
"""162号 P1-A集成 · T8 RAL接口 · router_r3.py 接线补丁。

接线点（2处）：
    ① T3 import 后：import ral_interface 模块
    ② RAL 状态标记：从 prepared_not_connected → active（创始人批示激活）

零破坏：不修改现有RAL逻辑，仅激活接口层。
"""
import os, shutil

ROUTER_PATH = "/home/agentuser/lao-release/lao/effect_anchored/routing/lao_router_server.py"
print(f"[T8-PATCH] Target: {ROUTER_PATH}")
bak = ROUTER_PATH + ".bak.t8"
if not os.path.exists(bak):
    shutil.copy2(ROUTER_PATH, bak)

with open(ROUTER_PATH, "r", encoding="utf-8") as f:
    src = f.read()

changes = 0

# ── ① Import T8 ──────────────
T8_IMPORT = '''
# ── P1-A T8 RAL最小接口（162号集成·2026-09-04） ──
try:
    from ral_interface import (
        RAL_MINIMAL_INTERFACES, get_interface_spec, validate_interface_name,
        SKILL_REGISTRY, DEFAULT_BOUNDARY
    )
    _T8_ENABLED = True
    _RAL_STATE = "active"  # 创始人批示：从 prepared_not_connected → active
except ImportError:
    _T8_ENABLED = False
    _RAL_STATE = "prepared_not_connected"
    print("[T8] ral_interface module not found")
'''

if "P1-A T8" not in src:
    anchor = '    print("[T3] context_pruning module not found")'
    if anchor in src:
        src = src.replace(anchor, anchor + "\n" + T8_IMPORT, 1)
        changes += 1
        print("[T8-PATCH] ① Import T8 + RAL activation — DONE")

# ── ② /health/ral 端点 ──────────────
T8_ENDPOINT = '''

# ── P1-A T8 RAL 状态端点（162号集成） ──
@app.get("/health/ral")
async def health_ral():
    """T8 RAL 接口健康检查。"""
    return {
        "t8_enabled": _T8_ENABLED,
        "ral_state": _RAL_STATE,
        "interfaces": list(RAL_MINIMAL_INTERFACES.keys()) if _T8_ENABLED else [],
        "skills": list(SKILL_REGISTRY.keys()) if _T8_ENABLED else [],
    }
'''

if "/health/ral" not in src:
    anchor = '@app.get("/health")'
    if anchor in src:
        src = src.replace(anchor, T8_ENDPOINT + "\n" + anchor, 1)
        changes += 1
        print("[T8-PATCH] ② /health/ral endpoint — DONE")

with open(ROUTER_PATH, "w", encoding="utf-8") as f:
    f.write(src)

print(f"\n[T8-PATCH] Applied {changes} changes")
print("[T8-PATCH] DONE")
