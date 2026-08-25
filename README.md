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

## 发布范围说明（v3.6.0-r3）

本仓库开源的是**配方**：LAO 的路由、认知锚点、经验闭环，以及它们的回归测试。**LAO 独立可运行，不依赖任何其他 ZWISERFIT 组件。**

以下内容**不在本次开源范围内**：

- **RIS（Agent Runtime Reliability Layer）**：运行时可靠性层，是**独立产品**，将另行发布，不含在本仓库内。LAO 与 RIS 之间通过一个只读的共享 JSON 文件契约通信（`RIS_BRIDGE_FILE`，默认 `~/shared/state/ris-bridge.json`）；该文件不存在时 LAO 全功能正常运行，仅不获得来自 RIS 的 provider 健康信号。本仓库保留的是 LAO 一侧的消费者实现（`ris_bridge_consumer.py`、`ris_health_gate.py`）与其回归测试，不含 RIS 本身。
- **排序权重数值**（`lao/effect_anchored/weights.json`）：加载接口与应用方式开源，权重数值不入库。文件缺失时回退为均权，功能可用但不含我们的认知偏置。
- **监控与门禁**：成本漂移告警、数据新鲜度告警、软启动门禁、单实例托管（systemd）、部署后版本校验、账单对账，均为运营侧脚本，开源版暂不包含。
- **内部运行数据**：事故记录、经验库、运行态快照、密钥与环境变量。

也就是说：开源版**包含产品代码内的保险丝**——重试上限与任务级 token 熔断、4xx 真实状态码透传、配对感知的上下文剪枝，以及 LAO 侧的桥数据陈旧保护（`RIS_BRIDGE_STALE_S`，默认 180 秒，桥文件超时即视为无信号并放行，fail-open）。**provider 隔离冷却（600 秒）与 provider 级数据时效过滤（900 秒）实现在 RIS 内，不随本仓库发布**；自建部署若需要这两层，需自备 provider 健康监控，或等 RIS 发布。运营侧的监控与门禁同样不包含，请自备监控。

本次为**带保险丝的发布**，不声称零缺陷。

### Scope of this release (English)

This repository open-sources the *recipe*: LAO routing, cognitive anchoring, the experience loop, and their regression tests. **LAO runs standalone and does not depend on any other ZWISERFIT component.**

Not included in this release:

- **RIS (Agent Runtime Reliability Layer)** — a **separate product**, shipped separately, not contained here. LAO talks to RIS only through a read-only shared JSON file contract (`RIS_BRIDGE_FILE`, default `~/shared/state/ris-bridge.json`). When that file is absent, LAO runs at full function and simply receives no provider-health signal from RIS. What this repo keeps is the LAO-side consumer (`ris_bridge_consumer.py`, `ris_health_gate.py`) and its regression tests — not RIS itself.
- **Ranking weight values** (`lao/effect_anchored/weights.json`) — the loading interface is open, the values are not committed. When the file is absent, the engine falls back to uniform weights: usable, but without our tuning.
- **Monitoring and gating** — cost-drift alerts, data-freshness alerts, soft-start gating, single-instance supervision (systemd), post-deploy version verification, and billing reconciliation are ops-side scripts and are not shipped here.
- **Internal runtime data** — incident records, experience stores, runtime snapshots, secrets and environment files.

In short: the fuses that live in the product code are included — retry ceiling and per-task token cutoff, real 4xx status pass-through, pair-aware context pruning, and LAO-side bridge staleness protection (`RIS_BRIDGE_STALE_S`, default 180s, fail-open). **Provider isolation cooldown (600s) and provider-level staleness filtering (900s) are implemented inside RIS and do not ship with this repository**; if you need those two layers in a self-hosted deployment, bring your own provider health monitoring or wait for the RIS release. Ops-side monitoring and gating are likewise not included — bring your own monitoring.

This is a **release with fuses**, not a claim of zero defects.

---

## Documentation

详见 `docs/` 与各模块 docstring。

## Contributing

遵循 [CONTRIBUTING.md](CONTRIBUTING.md)，欢迎 PR。

## License

Apache-2.0（以仓库根目录 LICENSE 文件为准）

## 隐私（Privacy）

**本版本不做任何遥测，导入与运行不产生对外网络回传。** 历史版本曾含一条默认开启的遥测（域名从未解析、无数据回收），已在 v3.6.0-r3 移除并如实披露——详见 [PRIVACY.md](PRIVACY.md)。
