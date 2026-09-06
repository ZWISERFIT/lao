#!/usr/bin/env python3
"""162号 P1-A集成 · T2任务身份层 · router_r3.py 接线补丁。

执行方式（Tristan 在服务器上运行）：
    cd /home/agentuser/lao-release  # 或 router_r3.py 所在目录
    python3 t2_patch_router_r3.py

接线点（5处）：
    ① 第43行后：import P1-A task_identity 模块
    ② 第319-332行：替换 _session_fingerprint() 为 SHA-256 版本
    ③ 第951-952行：_r3_task_key() 升级为 task_id 归因
    ④ 第955-980行：_r3_add_tokens() 增加 IsolationManager 隔离
    ⑤ 第1157-1202行：_settle_and_log() 增加 task_id 字段

零破坏原则：不修改现有 LAO 路由逻辑，仅增加 T2 身份层接线。
回滚：备份 router_r3.py.bak 已自动创建。
"""
import os
import re
import shutil

ROUTER_PATH = "/home/agentuser/lao-release/lao/effect_anchored/routing/lao_router_server.py"  # 165号修正
print(f"[T2-PATCH] Target: {ROUTER_PATH}")

# 备份
bak = ROUTER_PATH + ".bak.t2"
if not os.path.exists(bak):
    shutil.copy2(ROUTER_PATH, bak)
    print(f"[T2-PATCH] Backup → {bak}")
else:
    print(f"[T2-PATCH] Backup already exists: {bak}")

with open(ROUTER_PATH, "r", encoding="utf-8") as f:
    src = f.read()

changes = 0

# ── ① Import T2 模块（第43行 sys.path.insert 之后） ──────────────
T2_IMPORT = '''
# ── P1-A T2 统一任务身份层（162号集成·2026-09-04） ──
try:
    from task_identity import TaskIdentity, compute_session_fingerprint as _t2_fingerprint, IsolationManager
    _T2_ENABLED = True
except ImportError:
    _T2_ENABLED = False
    _t2_fingerprint = None
    IsolationManager = None
    print("[T2] task_identity module not found, falling back to legacy SHA1 fingerprint")
'''

if "P1-A T2" not in src:
    # 在 sys.path.insert 行后插入
    anchor = 'sys.path.insert(0, "/home/agentuser/lao-release")'
    if anchor in src:
        src = src.replace(anchor, anchor + "\n" + T2_IMPORT, 1)
        changes += 1
        print("[T2-PATCH] ① Import T2 module — DONE")
    else:
        print("[T2-PATCH] ① Import T2 — SKIP (anchor not found)")
else:
    print("[T2-PATCH] ① Import T2 — ALREADY APPLIED")

# ── ② 替换 _session_fingerprint() 为 SHA-256 版本 ──────────────
OLD_FP = '''def _session_fingerprint(messages: List[Dict]) -> str:
    """会话指纹: 首条 system + 首条 user 消息前 512 字符的 sha1(跨轮稳定)。"""
    import hashlib as _hl
    parts = []
    for want in ("system", "user"):
        for m in messages:
            if isinstance(m, dict) and m.get("role") == want:
                c = m.get("content", "")
                if not isinstance(c, str):
                    c = json.dumps(c, ensure_ascii=False, default=str)
                parts.append(c[:512])
                break
    raw = "\\x1f".join(parts)
    return _hl.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:16]'''

NEW_FP = '''def _session_fingerprint(messages: List[Dict]) -> str:
    """会话指纹（P1-A T2 升级：SHA-256 64位 hex·碰撞抗性 160→256 位）。

    162号集成·2026-09-04：优先使用 T2 task_identity.compute_session_fingerprint；
    不可用时回退到旧 SHA1[:16]（向后兼容·旧粘性 TTL=6h 自然过期）。
    """
    if _T2_ENABLED and _t2_fingerprint is not None:
        return _t2_fingerprint(messages)
    # 回退：旧 SHA1[:16]
    import hashlib as _hl
    parts = []
    for want in ("system", "user"):
        for m in messages:
            if isinstance(m, dict) and m.get("role") == want:
                c = m.get("content", "")
                if not isinstance(c, str):
                    c = json.dumps(c, ensure_ascii=False, default=str)
                parts.append(c[:512])
                break
    raw = "\\x1f".join(parts)
    return _hl.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:16]'''

if OLD_FP in src and "P1-A T2 升级" not in src:
    src = src.replace(OLD_FP, NEW_FP, 1)
    changes += 1
    print("[T2-PATCH] ② Replace _session_fingerprint() → SHA-256 — DONE")
elif "P1-A T2 升级" in src:
    print("[T2-PATCH] ② _session_fingerprint() — ALREADY APPLIED")
else:
    print("[T2-PATCH] ② _session_fingerprint() — SKIP (pattern not matched)")

# ── ③ _r3_task_key() 增加 task_id 支持 ──────────────
OLD_KEY = '''def _r3_task_key(agent: str, session_fp: str) -> str:
    return f"{agent or 'unknown'}|{session_fp or 'nofp'}"'''

NEW_KEY = '''def _r3_task_key(agent: str, session_fp: str, task_id: str = "") -> str:
    """R3 任务键（162号 T2 集成：优先用 task_id 归因）。"""
    if task_id:
        return f"t2|{task_id}"
    return f"{agent or 'unknown'}|{session_fp or 'nofp'}"'''

if OLD_KEY in src and "t2|" not in src:
    src = src.replace(OLD_KEY, NEW_KEY, 1)
    changes += 1
    print("[T2-PATCH] ③ _r3_task_key() add task_id — DONE")
elif "t2|" in src:
    print("[T2-PATCH] ③ _r3_task_key() — ALREADY APPLIED")
else:
    print("[T2-PATCH] ③ _r3_task_key() — SKIP")

# ── ④ _r3_add_tokens() 增加 IsolationManager 隔离 ──────────────
OLD_ADD = '''def _r3_add_tokens(agent: str, session_fp: str, tokens: int):
    """结算时累计任务 token · 越限即打告警/熔断标记(下一次请求入口拦截)。"""
    if tokens <= 0:
        return
    key = _r3_task_key(agent, session_fp)'''

NEW_ADD = '''def _r3_add_tokens(agent: str, session_fp: str, tokens: int, task_id: str = "",
                    is_side_task: bool = False):
    """结算时累计任务 token（162号 T2 集成：side 任务隔离不计入主任务护栏）。

    Args:
        is_side_task: T2 IsolationManager 判定为 side 任务时 True，
                      其 token 不计入主任务 R3 护栏。
    """
    if tokens <= 0:
        return
    # T2 隔离：side 任务 token 不进主任务护栏
    if is_side_task:
        _log_event({"type": "t2_side_token_isolated", "agent": agent or "unknown",
                    "task_id": task_id, "tokens": tokens,
                    "reason": "side task tokens isolated from main task R3 guard"})
        return
    key = _r3_task_key(agent, session_fp, task_id=task_id)'''

if OLD_ADD in src and "t2_side_token_isolated" not in src:
    src = src.replace(OLD_ADD, NEW_ADD, 1)
    changes += 1
    print("[T2-PATCH] ④ _r3_add_tokens() add IsolationManager — DONE")
elif "t2_side_token_isolated" in src:
    print("[T2-PATCH] ④ _r3_add_tokens() — ALREADY APPLIED")
else:
    print("[T2-PATCH] ④ _r3_add_tokens() — SKIP")

# ── ⑤ _settle_and_log() 增加 task_id 参数和事件字段 ──────────────
# 5a: 函数签名增加 task_id
OLD_SETTLE_SIG = '''                    status: str = "ok", error: str = "",
                    session_fp: str = ""):'''
NEW_SETTLE_SIG = '''                    status: str = "ok", error: str = "",
                    session_fp: str = "", task_id: str = "",
                    is_side_task: bool = False):'''

if OLD_SETTLE_SIG in src and "is_side_task" not in src:
    src = src.replace(OLD_SETTLE_SIG, NEW_SETTLE_SIG, 1)
    changes += 1
    print("[T2-PATCH] ⑤a _settle_and_log() signature — DONE")
elif "is_side_task" in src:
    print("[T2-PATCH] ⑤a _settle_and_log() signature — ALREADY APPLIED")
else:
    print("[T2-PATCH] ⑤a _settle_and_log() signature — SKIP")

# 5b: _r3_add_tokens 调用增加 task_id 和 is_side_task
OLD_R3_CALL = '''    try:
        _r3_add_tokens(agent, session_fp, int(in_tok or 0) + int(out_tok or 0))
    except Exception:
        pass'''
NEW_R3_CALL = '''    try:
        _r3_add_tokens(agent, session_fp, int(in_tok or 0) + int(out_tok or 0),
                       task_id=task_id, is_side_task=is_side_task)
    except Exception:
        pass'''

if OLD_R3_CALL in src and "is_side_task=is_side_task" not in src:
    src = src.replace(OLD_R3_CALL, NEW_R3_CALL, 1)
    changes += 1
    print("[T2-PATCH] ⑤b _r3_add_tokens call — DONE")
elif "is_side_task=is_side_task" in src:
    print("[T2-PATCH] ⑤b _r3_add_tokens call — ALREADY APPLIED")
else:
    print("[T2-PATCH] ⑤b _r3_add_tokens call — SKIP")

# 5c: 事件日志增加 task_id 字段
OLD_EVENT = '''        "session_fp": session_fp,
    })'''
NEW_EVENT = '''        "session_fp": session_fp,
        "task_id": task_id,
        "is_side_task": is_side_task,
    })'''

if OLD_EVENT in src and '"task_id": task_id' not in src:
    src = src.replace(OLD_EVENT, NEW_EVENT, 1)
    changes += 1
    print("[T2-PATCH] ⑤c Event log add task_id — DONE")
elif '"task_id": task_id' in src:
    print("[T2-PATCH] ⑤c Event log task_id — ALREADY APPLIED")
else:
    print("[T2-PATCH] ⑤c Event log task_id — SKIP")

# ── 写入 ──────────────────────────────────────────────────────────
with open(ROUTER_PATH, "w", encoding="utf-8") as f:
    f.write(src)

print(f"\n[T2-PATCH] Applied {changes} changes to {ROUTER_PATH}")
print(f"[T2-PATCH] Rollback: cp {bak} {ROUTER_PATH}")
print("[T2-PATCH] DONE")
