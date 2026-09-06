#!/usr/bin/env python3
"""162号 P1-A集成 · T5 Token字段字典 · router_r3.py 接线补丁。

接线点（3处）：
    ① T2 import 后：import token_dictionary 模块
    ② 全局初始化：创建 ProviderStatsRegistry 实例
    ③ _settle_and_log() 内：创建 TokenRecord 并喂入 Registry

零破坏：不修改现有命中率逻辑，仅并行记录标准化 Token 数据。
"""
import os, shutil

ROUTER_PATH = "/home/agentuser/lao-release/lao/effect_anchored/routing/lao_router_server.py"  # 165号修正
print(f"[T5-PATCH] Target: {ROUTER_PATH}")
bak = ROUTER_PATH + ".bak.t5"
if not os.path.exists(bak):
    shutil.copy2(ROUTER_PATH, bak)
    print(f"[T5-PATCH] Backup → {bak}")

with open(ROUTER_PATH, "r", encoding="utf-8") as f:
    src = f.read()

changes = 0

# ── ① Import T5 模块（在 T2 import 块之后） ──────────────
T5_IMPORT = '''
# ── P1-A T5 Token字段字典（162号集成·2026-09-04） ──
try:
    from token_dictionary import TokenRecord, ProviderStatsRegistry
    _T5_ENABLED = True
except ImportError:
    _T5_ENABLED = False
    TokenRecord = None
    ProviderStatsRegistry = None
    print("[T5] token_dictionary module not found, T5 disabled")
'''

if "P1-A T5" not in src:
    anchor = '    print("[T2] task_identity module not found, falling back to legacy SHA1 fingerprint")'
    if anchor in src:
        src = src.replace(anchor, anchor + "\n" + T5_IMPORT, 1)
        changes += 1
        print("[T5-PATCH] ① Import T5 module — DONE")
    else:
        print("[T5-PATCH] ① Import T5 — SKIP (T2 anchor not found)")
else:
    print("[T5-PATCH] ① Import T5 — ALREADY APPLIED")

# ── ② 全局 ProviderStatsRegistry 初始化 ──────────────
T5_REGISTRY = '''
# ── P1-A T5 Provider统计注册表（162号集成） ──
_t5_registry = None
if _T5_ENABLED and ProviderStatsRegistry is not None:
    _t5_registry = ProviderStatsRegistry()
    print("[T5] ProviderStatsRegistry initialized")
'''

if "_t5_registry" not in src:
    # 在 _T5_ENABLED 定义后插入
    anchor = '    print("[T5] token_dictionary module not found, T5 disabled")'
    if anchor in src:
        src = src.replace(anchor, anchor + "\n" + T5_REGISTRY, 1)
        changes += 1
        print("[T5-PATCH] ② ProviderStatsRegistry init — DONE")
    else:
        print("[T5-PATCH] ② ProviderStatsRegistry — SKIP")
else:
    print("[T5-PATCH] ② ProviderStatsRegistry — ALREADY APPLIED")

# ── ③ _settle_and_log() 内创建 TokenRecord ──────────────
# 在 _loop_record 调用后插入 T5 记录
T5_RECORD = '''    # ── P1-A T5: 标准化 TokenRecord 喂入 ProviderStatsRegistry ──
    if _T5_ENABLED and _t5_registry is not None and TokenRecord is not None:
        try:
            _t5_rec = TokenRecord.create(
                request_id=request_id, provider=provider, model=chosen_model,
                input_tokens=in_tok, output_tokens=out_tok,
                cache_hit_tokens=cache_hit, cache_miss_tokens=cache_miss,
                cost_yuan=cost_yuan, pricing_regime=pricing_regime,
                task_id=task_id, source="routing_settle")
            _t5_registry.record(_t5_rec)
        except Exception:
            pass  # T5 记录失败不影响主流程
'''

if "_t5_rec" not in src:
    anchor = '    _loop_record(provider, chosen_model, ok=(status == "ok"), error=error)'
    if anchor in src:
        src = src.replace(anchor, anchor + "\n" + T5_RECORD, 1)
        changes += 1
        print("[T5-PATCH] ③ TokenRecord in _settle_and_log — DONE")
    else:
        print("[T5-PATCH] ③ TokenRecord — SKIP (_loop_record anchor not found)")
else:
    print("[T5-PATCH] ③ TokenRecord — ALREADY APPLIED")

with open(ROUTER_PATH, "w", encoding="utf-8") as f:
    f.write(src)

print(f"\n[T5-PATCH] Applied {changes} changes")
print(f"[T5-PATCH] Rollback: cp {bak} {ROUTER_PATH}")
print("[T5-PATCH] DONE")
