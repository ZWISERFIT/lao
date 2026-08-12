# LAO v3.2 最新架构锚点（L1/L2/L3 · 创始人确认版）

> **状态：** 待创始人确认为锚点
> **日期：** 2026-08-12 21:26 CST
> **DRI：** Tristan（技术架构官）
> **说明：** 本文档=LAO 当前真实架构全貌（磁盘实证），以《LAO-three-layer-architecture-v3.1.html》为定位基准。
> **⚠️ 旧三层（NovaRouteAI→LAO→决策树）已废弃：** NovaRouteAI 只是（L1 的）一个 Provider，不是架构顶层。

---

## 〇、一页看懂 LAO

**LAO = 让 Agent「像人一样靠谱」的人性校准层（Trust Layer）。**
Agent 装上 LAO 之前：忘事、胡说、行为失控。装上之后：三层机制让 Agent 行为可信、经验可沉淀、价值可确权。

```
        ┌────────────────────────────────────────────┐
        │                 LAO v3.2                    │
        │                                            │
        │   L1  路由决策   → 选对模型/Provider、可审计   │
        │   L2  经验工厂   → 认知沉淀、约束进化、记忆锚   │
        │   L3  确权交易   → 经验证明、产权确权、市场流通  │
        │                                            │
        └────────────────────────────────────────────┘
              ▲             ▲             ▲
         (开源协议)     (开源结构)     (开源协议/验证)
         (采购表闭源)   (权重/配方闭源)  (估值/定价闭源)
```

**一句话边界：** LAO 开源「规则和接口」，开放「可信验证能力」，闭源「让 Agent 产生经济价值的核心策略」。

---

## 一、总体规模（磁盘实证）

- **工作仓：** `lao-release/` → 68 个 `.py` 文件，共 **12,409 行**
- **主入口：** `LAOAgent`（lao/__init__.py，六函数：watch / record_intention / predict / add_constraint / check / watch_and_see）
- **GitHub 公开仓（origin/main）：** 已含 65/68 个 py；未公开 3 个 = attestation 协议层（新建·待 v3.2 发）+ closed_loop 测试

---

## 二、L1 路由决策层

**职责：** 根据任务选对模型/Provider → 构建跨 Provider 降级链路，行为可审计。

| 文件 | 职责 |
|:--|:--|
| `model_router.py` (331行) | ⭐核心。三级选品算法：安全(SAFETY_GATE质量底线) → 效率(latency) → 成本(cost)。**v3.2 已改：支持 `model_pool` 注入**（真实采购表 OS 私有）|
| `task_classifier.py` | 功耗感知式任务分类（8 tier：ultra_light/light/medium/heavy/reasoning/code/cn_explain/cn_creative）|
| `task_decomposer.py` (378行) | 复杂指令拆解·多模型组合路由 |
| `cost_tracker.py` | 路由调用成本日志 + Nova 成本同步 |
| `switch_audit.py` | 切换审计（模型切换可追溯·P1-14）|
| `timeout_matrix.py` | 时延矩阵（超时处理·P1-15）|
| `agent_reliability.py` | Provider 四维可靠性评分(0-100) |

**v3.2 边界（#2）：**
- 🟢 开源：选型算法、RouterDecision 接口、评分维度(quality/latency/cost)、故障转移机制、泛化 provider_a/b/c
- 🔴 闭源（OS 私有 `routing_policy.py`）：真实 Provider 名(deepseek/token-plan/novarouteai) + 真实价格曲线 + Routing Policy Engine

---

## 三、L2 经验工厂层

**职责：** 让 Agent 把经验沉淀为认知锚 + 约束 + 记忆，行为模式可进化。

| 文件 | 职责 |
|:--|:--|
| `cognitive_engine.py` | ⭐三层认知系统：L1实时迭代(冲突修正/错误复利/经验复利) + L2短期品味(修养/见识/情感) + L3长期判断(世界观/价值观/人生观)。**v3.2 已改：`CognitivePolicy` 可注入** |
| `cognitive_anchor.py` | 认知锚（锚点沉淀）|
| `feedback_bus.py` | 反馈总线（三层融合检索）|
| `memory_anchor.py` | 记忆锚 |
| `evolution/*` | atom_engine(自动沉淀) / constraint_generator / experience_extractor / rule_registry |
| `adaptive_constraint.py` | 自适应约束 |
| `context_rebuilder.py` | 上下文重建 |
| `data_cleanser.py` | 数据清洗 |
| `hallucination_gate.py` | 幻觉拦截 |

**v3.2 边界（#4/5）：**
- 🟢 开源：认知架构、锚 schema、反馈协议、L1/L2/L3 结构、示例权重
- 🔴 闭源：真实配比/权重（`weights.json`：trust=0.32/freshness=0.18/relevance=0.25/rarity=0.15/trigger=0.10，**文档标注"创始人认知系统核心·不开源"，未进 GitHub**）、硬化/复利策略、Trust 衰减、经验价值函数、Founder Calibration

---

## 四、L3 确权交易层

**职责：** 经验真实性证明 + 产权确权 + 市场流通（Web5 价值载体）。

| 文件 | 职责 |
|:--|:--|
| `attestation/protocol.py` | ⭐**v3.2 新建(#6/7)**：ExperienceAttestationProtocol(evaluate) + ExperienceOwnershipProtocol(attest/verify) + ExperienceScore 公开结构 |
| `experience_contract.py` | 经验契约 |
| `experience_graph.py` | 经验关系图 |
| `experience_matching.py` | 经验匹配 |
| `experience_readiness.py` | 经验就绪度 |
| `ethan/experience_evaluator.py` | Ethan 估值客户端(port 17800) |
| `melody_builder.py` / `melody_interface.py` / `melody/*` | Melody 市场构件(展示/定价/交易) |

**v3.2 边界（#6/7/#8）：**
- 🟢 开源：ExperienceContract / Consent / Attestation / TrustEvent / Hash / Verification / Permission / Ownership / ExperienceScore 公开结构
- 🔴 闭源：评分配方权重、稀有度/区间参考、商业估值、定价、市场数据、DID 实现 + 哈希水印 + Web5 身份（归 ZWISERFIT-OS）
- **Melody 边界：** LAO 管「这是什么资产、是谁的、是否真实、能否使用」；Melody 管「如何展示、组合、定价、交易、授权、流通」

---

## 五、核心配方坐标（🔒必须锁死·A 版内部）

| 🔒 配方 | 位置 | 是否已公开 |
|:--|:--|:--|
| 认知检索权重(trust/freshness/relevance/rarity/trigger) | `weights.json` | 🟢 **未公开·安全** |
| BMC 行为模式约束(7年门店经验) | `C-BMC-001-*.json` | 🟢 **未公开·安全** |
| 认知层权重(W_L1/L2/L3) | `cognitive_engine.py` | 🔴 已公开(0cb184a历史) |
| 路由采购表/价格 | `model_router.py` | 🔴 已公开，v3.2已抽离OS私有 |
| 路由时延矩阵 | `timeout_matrix.json` | 🔴 已公开 |

---

## 六、协议适配层（ZWISERFIT-OS 侧·v3.2 #9）

```
ZWISERFIT-OS ──► LAO Protocol Adapter ──► LAO API（协议）
                   ├─ RoutingProtocol            (L1)
                   ├─ CognitivePolicyProtocol    (L2)
                   ├─ TrustEventProtocol         (全链路)
                   ├─ ExperienceAttestationProtocol (L3)
                   └─ ExperienceOwnershipProtocol (L3)
```

OS **不 import LAO 内部类**，走协议适配。LAO 可一直开源演进，OS 不被内部实现绑死。

---

## 七、待创始人确认的锚点要点

1. ✅ 当前真实架构 = **L1 路由 / L2 经验工厂 / L3 确权交易**（已确认）
2. ⚠️ 已公开历史收不回：认知层权重(0.40/0.35/0.25)、路由采购、时延矩阵已进 GitHub
3. ✅ 可锁死：weights.json 认知权重、BMC 行为模式还在私有侧
4. ⏳ v3.2 3 项已改(本地待发)：CognitivePolicy / model_pool / Attestation
5. ⏳ 待过档：一份「A 真实 / B 开源」两版本审阅材料（发 Richard + AI 开发圈）

---

*本文档为 LAO v3.2 真实架构锚点。确认后作为后续所有优化/审阅的唯一基准。*
