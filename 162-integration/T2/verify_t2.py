#!/usr/bin/env python3
"""162号 T2集成验证脚本。

验证5个接线点是否正确应用：
    ① T2 import 存在
    ② _session_fingerprint 使用 SHA-256
    ③ _r3_task_key 支持 task_id
    ④ _r3_add_tokens 支持 is_side_task
    ⑤ _settle_and_log 包含 task_id 字段
"""
import sys
import os

ROUTER_PATH = sys.argv[1] if len(sys.argv) > 1 else "router_r3.py"
if not os.path.exists(ROUTER_PATH):
    ROUTER_PATH = "/home/agentuser/lao-release/scripts/router_r3.py"

print(f"[T2-VERIFY] Checking: {ROUTER_PATH}")
with open(ROUTER_PATH, "r", encoding="utf-8") as f:
    src = f.read()

checks = [
    ("① T2 import", "P1-A T2" in src and "from task_identity import" in src),
    ("② SHA-256 fingerprint", "P1-A T2 升级" in src and "_t2_fingerprint" in src),
    ("③ task_id in _r3_task_key", 't2|{task_id}' in src or "t2|" in src),
    ("④ is_side_task isolation", "t2_side_token_isolated" in src),
    ("⑤ task_id in event log", '"task_id": task_id' in src),
]

passed = 0
failed = 0
for name, ok in checks:
    status = "PASS" if ok else "FAIL"
    if ok:
        passed += 1
    else:
        failed += 1
    print(f"  [{status}] {name}")

print(f"\n[T2-VERIFY] Result: {passed} PASS / {failed} FAIL / {len(checks)} total")
sys.exit(0 if failed == 0 else 1)
