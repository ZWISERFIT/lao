#!/usr/bin/env python3
"""165号修正：修复T2/T5补丁的ROUTER_PATH"""
import re, os

BASE = "/home/agentuser/lao-release/162-integration"
CORRECT_PATH = "/home/agentuser/lao-release/lao/effect_anchored/routing/lao_router_server.py"

for f in ["T2/patches/t2_patch_router_r3.py", "T5/patches/t5_patch_router_r3.py"]:
    p = os.path.join(BASE, f)
    with open(p, "r", encoding="utf-8") as fh:
        lines = fh.readlines()
    
    new_lines = []
    skip_until_print = False
    for line in lines:
        # Remove the bad 165 override line (with space issue)
        if "165" in line and "ROUTER_PATH" in line:
            continue
        # Skip the fallback chain lines after os.path.join
        if skip_until_print:
            if 'print(' in line:
                skip_until_print = False
                new_lines.append(line)
            continue
        # Replace the os.path.join line
        if line.strip().startswith("ROUTER_PATH = os.path.join(os.path.dirname"):
            new_lines.append(f'ROUTER_PATH = "{CORRECT_PATH}"  # 165号修正\n')
            skip_until_print = True
            continue
        new_lines.append(line)
    
    with open(p, "w", encoding="utf-8") as fh:
        fh.writelines(new_lines)
    print(f"Fixed {f}: ROUTER_PATH = {CORRECT_PATH}")

print("Done")
