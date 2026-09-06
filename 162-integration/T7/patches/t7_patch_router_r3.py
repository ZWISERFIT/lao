#!/usr/bin/env python3
"""162号 P1-A集成 · T7任务状态机 · router_r3.py 接线补丁。

接线点（3处）：
    ① T5 import 后：import task_state_machine 模块
    ② 全局初始化：StopLossMonitor + TaskStateMachine 注册表
    ③ _r3_add_tokens() 内：并行使用 T7 StopLossMonitor

零破坏：不替换现有止损逻辑，仅并行记录。
"""
import os, shutil

ROUTER_PATH = "/home/agentuser/lao-release/lao/effect_anchored/routing/lao_router_server.py"
print(f"[T7-PATCH] Target: {ROUTER_PATH}")
bak = ROUTER_PATH + ".bak.t7"
if not os.path.exists(bak):
    shutil.copy2(ROUTER_PATH, bak)

with open(ROUTER_PATH, "r", encoding="utf-8") as f:
    src = f.read()

changes = 0

# ── ① Import T7 ──────────────
T7_IMPORT = '''
# ── P1-A T7 任务状态机（162号集成·2026-09-04） ──
try:
    from task_state_machine import TaskStateMachine, StopLossMonitor, StopLossConfig, TaskState
    _T7_ENABLED = True
except ImportError:
    _T7_ENABLED = False
    print("[T7] task_state_machine module not found")
'''

if "P1-A T7" not in src:
    anchor = '    print("[T5] token_dictionary module not found, T5 disabled")'
    if anchor in src:
        src = src.replace(anchor, anchor + "\n" + T7_IMPORT, 1)
        changes += 1
        print("[T7-PATCH] ① Import T7 — DONE")

# ── ② 全局 StopLossMonitor ──────────────
T7_MONITOR = '''
# ── P1-A T7 止损监控（162号集成） ──
_t7_monitor = None
_t7_machines = {}  # task_id → TaskStateMachine
if _T7_ENABLED:
    _t7_monitor = StopLossMonitor(StopLossConfig())
    print("[T7] StopLossMonitor initialized")
'''

if "_t7_monitor" not in src:
    anchor = '    print("[T7] task_state_machine module not found")'
    if anchor in src:
        src = src.replace(anchor, anchor + "\n" + T7_MONITOR, 1)
        changes += 1
        print("[T7-PATCH] ② StopLossMonitor init — DONE")

# ── ③ _r3_add_tokens 内并行使用 T7 ──────────────
T7_ADD = '''    # ── P1-A T7: StopLossMonitor 并行记录 ──
    if _T7_ENABLED and _t7_monitor is not None and task_id:
        try:
            t7_result = _t7_monitor.add_tokens(task_id, int(in_tok or 0) + int(out_tok or 0))
            if t7_result.get("new_alert"):
                _log_event({"type": "t7_stop_alert", "task_id": task_id,
                            "tokens": t7_result["tokens"],
                            "threshold": _t7_monitor.config.alert_tokens})
            if t7_result.get("broken"):
                _log_event({"type": "t7_stop_breaker", "task_id": task_id,
                            "tokens": t7_result["tokens"],
                            "threshold": _t7_monitor.config.hard_tokens})
        except Exception:
            pass
'''

if "_t7_monitor.add_tokens" not in src:
    anchor = '    # ── P1-A T5: 标准化 TokenRecord 喂入 ProviderStatsRegistry ──'
    if anchor in src:
        src = src.replace(anchor, T7_ADD + anchor, 1)
        changes += 1
        print("[T7-PATCH] ③ T7 StopLossMonitor in _r3_add_tokens — DONE")

with open(ROUTER_PATH, "w", encoding="utf-8") as f:
    f.write(src)

print(f"\n[T7-PATCH] Applied {changes} changes")
print("[T7-PATCH] DONE")
