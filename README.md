# LAO — Logically Anchored Ontology

**让 Agent 不再忘事、不再胡说，像人一样记住真实经验并自动复利。**

LAO 是一个开源的人类校准层 + Agent 可靠性框架。它把 **创始人 7 年真实经营智慧** 编码为可验证的认知锚点，让开发者构建的 Agent ：
- 🧠 **不再忘事** — Behavioral Memory Chain 记住身份/约束/偏好
- 🤥 **不再胡说** — Intent Validation + Output Compliance 检测幻觉
- 💸 **不再烧钱** — Key Anchor Engine 智能裁剪上下文噪声
- 🔁 **自动复利** — 每次错误自动萃取为永久约束（Feedback Bus）

---

## 两层架构：用户演示层 + Agent 可靠性层

LAO 由两个互补层组成，合仓后是一个完整包：

### 1️⃣ BMC — 行为预测引擎（用户演示层）
`lao/core/`
- 演示"积累了足够多行为经验后 LAO 能做什么"
- Behavioral Markov Chain + Human Nature Engine：预测 Agent 在给定上下文下一步行为

### 2️⃣ 五引擎 — Agent 可靠性层（Trust Kernel）
`lao/effect_anchored/`

| 引擎 | 模块 | 作用 |
|:--|:--|:--|
| **L1 智能路由** | `routing/model_router.py` | 三 provider（DeepSeek/TokenPlan/NovaRouteAI）故障转移，跨 provider 先验证模型存在 |
| **L2 认知锚点** | `cognitive_anchor.py` | Fact→Decision→Cognitive 三层递进，从"记规则"到"理解为什么" |
| **L3 经验原子** | `evolution/atom_engine.py` | Trust Event → Atom → Anchor → Future Protection 复利闭环 |
| **L2 偏好防火墙** | `preference_firewall.py` | 效率优化允许，身份/价值表达禁止 |
| **经验图** | `experience_graph.py` | similar_to / caused_by / derived_from 关系网络 |
| **反馈总线** | `feedback_bus.py` | L3经验→L2锚点→L1路由 自动闭环回流（自动萃取复利） |
| **经验契约** | `experience_contract.py` | 经验共享安全边界，防跨域污染 |
| **经验检索** | `experience_matching.py` | `retrieve_verified_experience()` 带权限/契约过滤的已验证经验检索（Melody 接入点） |

> **Same Agent, Different Human** — 同一 LAO Agent 面向不同 Human 时，检索到「已验证但差异化」的经验集。差异来自各自 Human 的契约锚点（Storage 层），LAO 检索保持真实验证，不做偏好推断（那是 Melody 的 Matching/Personal Adaptation 域）。运行 `python examples/same_agent_different_human.py` 查看演示。

---

## 🧠 自带创始人认知锚点（开箱即用）

LAO **自带创始人 7 年真实运营经验编码的 Cognitive Anchors**。不是空框架——开发者可以直接在真实经验上构建：

```
示例 DecisionAnchor:
  principle: "客户信任优先于短期收入"
  trigger_condition: "投诉涉及退款>¥500"
  action_rule: 人工介入·创始人决策
  counter_examples: ["低风险投诉可自动处理"]
  derived_from_events: ["2024年3月退款纠纷"]

示例 CognitiveAnchor:
  principle: "短期损失优先保护长期信任资产"
  applicability: ["客户纠纷", "退款", "投诉"]
```

**不开源范围：** 仅 ZWISERFIT 实时门店数据（会员流/营收流）为商业敏感数据，不属于 LAO 范畴。

---

## 3 lines. 3 minutes. See the difference.

```bash
pip install lao-human-calibration
```

```bash
# 初始化 LAO runtime（含创始人认知锚点）
lao init

# 记录一个 Trust Event（经验原子入口）
lao trust-event --text "客户投诉退款600元，创始人决定人工介入"

# 查看锚点状态
lao status

# Experience Atom: Trust Event → Atom → Anchor → Future Protection
lao atom

# Preference Firewall: 效率优化允许 / 身份价值变更拒绝
lao firewall
```

---

## 快速集成（Python）

```python
from lao import LAOAgent

# 创建 LAO Agent（人性校准层）
ai = LAOAgent()

# 记录用户行为 → 预测下一步
ai.watch("user_001", "客户投诉退款600元")
prediction = ai.predict("user_001")
print(prediction)  # 行为预测
```

---

## What makes LAO different?

**普通 Memory:** "Suzanne 喜欢快速回复"
**LAO Anchor:** "Suzanne 的经营原则：客户信任优先于短期收入·高风险投诉人工介入·低风险自动解决"

这不是数据。是 **Decision Logic** —— 它决定了 *为什么这么做*，不是 *说过什么*。

---

## We eat our own dog food

ZWISERFIT 9-Agent Collective 全栈跑在自己的 LAO 上——每个 Agent 的每次错误都自动萃取为永久约束，形成复利。我们的 LAO 框架自身也用 LAO 构建和验证。

---

## 资源

- **ERGE 检索引擎**: 运行时按需注入认知锚点，不污染 AGENTS.md
- **标签系统**: `data/ZWISERFIT/cognitive-os/anchor-tags.yaml`
- **审计**: Stella 独立审计签名链

---

## 仓库合并说明（2026-08-17）

本仓库（`ZWISERFIT/lao`）是 LAO 的**唯一官方仓库**。原 `ZWISERFIT/lineage-anchored-ontology` 仓库（行为记忆层/谱系锚定本体）已于 2026-08-17 整体并入本仓库：其核心代码（`effect_anchored/`）、测试、demo 与示例资产均已合入，原仓库仅保留指路说明并归档。PyPI 正式包为 `lao-human-calibration`；原 alpha 包 `lineage-anchored-ontology` 已标记 deprecated 指向本包。

历史 import 路径迁移：`from effect_anchored import ...` → `from lao.effect_anchored import ...`。

---

## Documentation

详见 `docs/` 与各模块 docstring。

## Contributing

遵循 [CONTRIBUTING.md](CONTRIBUTING.md)，欢迎 PR。

## License

Apache-2.0 · MIT (PoPB 协议)
