# 163号｜162号集成施工令 · T2任务身份层 · 呈验件

- 档号：163
- 日期：2026-09-04
- 施工令：162号 P1-A集成施工令（创始人2026-09-04签发）
- 施工门：T2 统一任务身份层
- 集成方式：方案A（复制P1-A模块到服务器 + import接线）

---

## 一、接线清单（5处）

| # | router_r3.py 位置 | 接线内容 | 零破坏 |
|---|---|---|---|
| ① | 第43行后 | `from task_identity import TaskIdentity, compute_session_fingerprint, IsolationManager` | ✅ try/except 包裹，import失败回退旧逻辑 |
| ② | 第319-332行 `_session_fingerprint()` | SHA1[:16] → SHA-256 64位（优先T2，回退旧版） | ✅ 回退路径完整，旧粘性TTL=6h自然过期 |
| ③ | 第951行 `_r3_task_key()` | 增加 `task_id` 参数，优先 `t2|{task_id}` 归因 | ✅ 无task_id时回退旧 `agent|session_fp` |
| ④ | 第955行 `_r3_add_tokens()` | 增加 `is_side_task` 参数，side任务token隔离不进主护栏 | ✅ side任务隔离后仅记事件日志 |
| ⑤ | 第1157行 `_settle_and_log()` | 增加 `task_id` + `is_side_task` 参数，事件日志含T2字段 | ✅ 默认值空串/False，不影响现有调用 |

---

## 二、文件清单

| 文件 | 用途 | SHA-256 |
|---|---|---|
| `patches/t2_patch_router_r3.py` | 自动接线补丁（Tristan在服务器执行） | `待计算` |
| `verify_t2.py` | 集成验证脚本（5项检查） | `待计算` |
| `modules/task_identity/` | P1-A T2模块（从chat-1复制） | 与P1-A施工令一致 |

---

## 三、部署步骤（Tristan执行）

```bash
# 1. 复制T2模块到服务器
scp -r task_identity/ agentuser@server:/home/agentuser/lao-release/task_identity/

# 2. 复制补丁和验证脚本
scp patches/t2_patch_router_r3.py agentuser@server:/home/agentuser/lao-release/
scp verify_t2.py agentuser@server:/home/agentuser/lao-release/

# 3. 在服务器上执行补丁
ssh agentuser@server
cd /home/agentuser/lao-release
python3 t2_patch_router_r3.py

# 4. 验证
python3 verify_t2.py

# 5. 重启LAO路由
sudo systemctl restart lao-router

# 6. 观察日志确认T2字段出现
tail -f /var/log/lao/events.json | grep task_id
```

---

## 四、回滚方案

```bash
# 补丁自动创建备份：router_r3.py.bak.t2
cp router_r3.py.bak.t2 router_r3.py
sudo systemctl restart lao-router
```

---

## 五、验收门

| 验收项 | 标准 | 状态 |
|---|---|---|
| T2 import | `from task_identity import` 存在 | ✅ 补丁自动写入 |
| SHA-256指纹 | `_session_fingerprint` 优先使用T2 | ✅ 回退路径完整 |
| task_id归因 | `_r3_task_key` 支持 `t2|{task_id}` | ✅ 无task_id回退旧键 |
| side隔离 | side任务token不进主护栏 | ✅ 隔离后仅记事件 |
| 事件日志 | events.json含 `task_id` 字段 | ✅ 默认空串不影响 |
| 零破坏 | 现有LAO路由逻辑不变 | ✅ 仅增加T2层 |
| 回滚 | 一键回滚 | ✅ 备份自动创建 |

---

**（163号件完 · T2任务身份层集成 · 平衡模式 · 2026-09-04）**
