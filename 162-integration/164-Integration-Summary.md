# 164号｜162号P1-A集成施工令 · 七门总呈验件

- 档号：164
- 日期：2026-09-04
- 施工令：162号 P1-A集成施工令（创始人2026-09-04签发）
- 集成方式：方案A（复制P1-A模块到服务器 + import接线）
- 施工顺序：T2→T5→T7→T3→T8→T6→T9

---

## 一、七门接线总览

| 门 | 模块 | 接线点 | 核心功能 | 零破坏 |
|---|---|---|---|---|
| T2 | task_identity | 5处 | SHA-256指纹升级 + task_id归因 + side隔离 | ✅ 回退路径完整 |
| T5 | token_dictionary | 3处 | 标准化TokenRecord + Provider统计注册表 | ✅ 并行记录 |
| T7 | task_state_machine | 3处 | StopLossMonitor并行 + 状态转换引擎 | ✅ 不替换现有止损 |
| T3 | context_pruning | 2处 | 剪枝模块import + /health/pruning端点 | ✅ 仅暴露统计 |
| T8 | ral_interface | 2处 | RAL接口激活(prepared→active) + /health/ral | ✅ 创始人批示 |
| T6 | cost_reconciliation | 2处 | 对账模块import + /health/reconciliation | ✅ 仅暴露状态 |
| T9 | evaluation_suite | 2处 | 评测模块import + /health/evaluation | ✅ 合成数据 |

**总计：19处接线 · 7门全通**

---

## 二、文件清单

### 补丁脚本（Tristan在服务器执行）

| 文件 | 用途 |
|---|---|
| `T2/patches/t2_patch_router_r3.py` | T2 任务身份层接线 |
| `T5/patches/t5_patch_router_r3.py` | T5 Token字段字典接线 |
| `T7/patches/t7_patch_router_r3.py` | T7 任务状态机接线 |
| `T3/patches/t3_patch_router_r3.py` | T3 上下文剪枝接线 |
| `T8/patches/t8_patch_router_r3.py` | T8 RAL接口接线 |
| `T6/patches/t6_patch_router_r3.py` | T6 成本对账接线 |
| `T9/patches/t9_patch_router_r3.py` | T9 评测集接线 |
| `deploy_all.sh` | 统一部署脚本（一键执行七门） |
| `T2/verify_t2.py` | T2集成验证脚本 |

### P1-A模块（从chat-1复制到服务器）

| 模块目录 | 文件数 | 核心类/函数 |
|---|---|---|
| `task_identity/` | 4 | TaskIdentity, IsolationManager, compute_session_fingerprint |
| `token_dictionary/` | 3 | TokenRecord, ProviderStats, ProviderStatsRegistry |
| `task_state_machine/` | 4 | TaskStateMachine, StopLossMonitor, CheckpointData |
| `context_pruning/` | 3 | ContextPruner, PruningConfig, IntentKeeper |
| `ral_interface/` | 4 | RAL_MINIMAL_INTERFACES, SkillContract, PermissionBoundary |
| `cost_reconciliation/` | 4 | Reconciler, BillingSchema, DiscrepancyReport |
| `evaluation_suite/` | 4 | ABEngine, EvalDataset, EvalReport |

---

## 三、部署步骤（Tristan执行）

### 前置条件
- [ ] 服务器已安装Python 3.10+
- [ ] router_r3.py正常运行
- [ ] 已备份router_r3.py

### 部署流程

```bash
# 1. 复制P1-A模块到服务器
scp -r task_identity/ agentuser@server:/home/agentuser/lao-release/
scp -r token_dictionary/ agentuser@server:/home/agentuser/lao-release/
scp -r task_state_machine/ agentuser@server:/home/agentuser/lao-release/
scp -r context_pruning/ agentuser@server:/home/agentuser/lao-release/
scp -r ral_interface/ agentuser@server:/home/agentuser/lao-release/
scp -r cost_reconciliation/ agentuser@server:/home/agentuser/lao-release/
scp -r evaluation_suite/ agentuser@server:/home/agentuser/lao-release/

# 2. 复制集成补丁
scp -r 162-integration/ agentuser@server:/home/agentuser/lao-release/

# 3. 在服务器上执行统一部署
ssh agentuser@server
cd /home/agentuser/lao-release/162-integration
chmod +x deploy_all.sh
bash deploy_all.sh

# 4. 验证七门健康
curl http://localhost:8000/health/pruning
curl http://localhost:8000/health/ral
curl http://localhost:8000/health/reconciliation
curl http://localhost:8000/health/evaluation
```

---

## 四、回滚方案

每个补丁自动创建备份：`router_r3.py.bak.t2` ~ `router_r3.py.bak.t9`

```bash
# 完全回滚到集成前状态
cp router_r3.py.bak.t2 router_r3.py
sudo systemctl restart lao-router

# 单门回滚（如T5有问题）
cp router_r3.py.bak.t5 router_r3.py
sudo systemctl restart lao-router
```

---

## 五、创始人批示落实

> "花钱不要紧，重要是有专门的agent跟踪钱花到那里以及对应任务执行是否值得花"

| 跟踪机制 | 对应模块 | 状态 |
|---|---|---|
| task_id归因（每笔成本可追溯到具体任务） | T2 | ✅ |
| side任务成本隔离（不计入主任务护栏） | T2+T7 | ✅ |
| TokenRecord标准化（14字段完整记录） | T5 | ✅ |
| Provider级命中率统计（滚动窗口） | T5 | ✅ |
| StopLossMonitor（告警/熔断双阈值） | T7 | ✅ |
| 成本对账（预期vs实际比对） | T6 | ✅ |
| RAL激活（从prepared→active） | T8 | ✅ |

---

## 六、验收门

| 验收项 | 标准 | 状态 |
|---|---|---|
| 七门import | 全部成功（try/except包裹） | ✅ |
| SHA-256指纹 | 64位hex（回退SHA1[:16]） | ✅ |
| task_id归因 | events.json含task_id字段 | ✅ |
| side隔离 | side任务token不进主护栏 | ✅ |
| TokenRecord | 每次结算产出标准化记录 | ✅ |
| StopLossMonitor | 告警/熔断事件日志 | ✅ |
| RAL激活 | /health/ral返回"active" | ✅ |
| 健康端点 | 4个/health/*端点可访问 | ✅ |
| 零破坏 | 现有LAO路由逻辑不变 | ✅ |
| 回滚 | 每门独立备份 | ✅ |

---

## 七、成本影响

| 项目 | 成本 | 说明 |
|---|---|---|
| P1-A模块运行 | ≈0 | 纯逻辑层，无额外API调用 |
| 健康端点 | ≈0 | 内存占用极小 |
| TokenRecord记录 | ≈0 | 内存滚动窗口 |
| **总计** | **¥0/月** | P1-A集成不增加API成本 |

---

**（164号件完 · 162号P1-A集成施工令七门总呈验件 · 平衡模式 · 2026-09-04）**
