#!/usr/bin/env python3
"""162号 P1-A集成 · T6成本对账 · router_r3.py 接线补丁。

接线点（2处）：
    ① T8 import 后：import cost_reconciliation 模块
    ② /health/reconciliation 端点：暴露对账状态

零破坏：不修改现有结算逻辑，仅并行记录对账数据。
"""
import os, shutil

ROUTER_PATH = "/home/agentuser/lao-release/lao/effect_anchored/routing/lao_router_server.py"
print(f"[T6-PATCH] Target: {ROUTER_PATH}")
bak = ROUTER_PATH + ".bak.t6"
if not os.path.exists(bak):
    shutil.copy2(ROUTER_PATH, bak)

with open(ROUTER_PATH, "r", encoding="utf-8") as f:
    src = f.read()

changes = 0

# ── ① Import T6 ──────────────
T6_IMPORT = '''
# ── P1-A T6 成本对账（162号集成·2026-09-04） ──
try:
    from cost_reconciliation import (
        Reconciler, ReconciliationConfig, CostRecord,
        BILLING_SCHEMAS, get_billing_schema
    )
    _T6_ENABLED = True
except ImportError:
    _T6_ENABLED = False
    print("[T6] cost_reconciliation module not found")
'''

if "P1-A T6" not in src:
    anchor = '    print("[T8] ral_interface module not found")'
    if anchor in src:
        src = src.replace(anchor, anchor + "\n" + T6_IMPORT, 1)
        changes += 1
        print("[T6-PATCH] ① Import T6 — DONE")

# ── ② /health/reconciliation 端点 ──────────────
T6_ENDPOINT = '''

# ── P1-A T6 对账状态端点（162号集成） ──
@app.get("/health/reconciliation")
async def health_reconciliation():
    """T6 成本对账健康检查。"""
    return {
        "t6_enabled": _T6_ENABLED,
        "providers": list(BILLING_SCHEMAS.keys()) if _T6_ENABLED else [],
        "config": {
            "reconciliation_interval_sec": 86400,
            "discrepancy_threshold_yuan": 0.01,
        } if _T6_ENABLED else None,
    }
'''

if "/health/reconciliation" not in src:
    anchor = '@app.get("/health")'
    if anchor in src:
        src = src.replace(anchor, T6_ENDPOINT + "\n" + anchor, 1)
        changes += 1
        print("[T6-PATCH] ② /health/reconciliation endpoint — DONE")

with open(ROUTER_PATH, "w", encoding="utf-8") as f:
    f.write(src)

print(f"\n[T6-PATCH] Applied {changes} changes")
print("[T6-PATCH] DONE")
