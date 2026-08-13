# LAO v3.3 真实代码级架构修复 · 完整审计材料（ChatGPT 审核用）

> 版本: **v3.3**（git: v3.1-final + 14 commits → fbdfa64）· DRI: Tristan · 2026-08-13 12:5x
> 审计对象: **完整 LAO 闭源代码架构**（13,203 行 / 70+ py 文件）
> 核心: 标注①上一轮 ChatGPT 意见修复 ②我们自己的修复 — 全面审阅

---

## 〇、审计对象 = 完整闭源代码（非阉割）

完整 `lao/` 代码树（13,203 行·全部可审计）：

```
lao/
├── core/                    # L0 内核 · 行为马氏链/意图衰减/决策
│   ├── behavioral_markov_chain.py  router_decision.py
│   └── behavioral_tokenizer.py     intention_decay.py  human_nature_engine.py
├── protocol/                # ★ L1 协议层(开源·可审计契约)
│   └── contracts.py
├── open/  private/          # 开源声明 / 闭源私有策略(weights/C-BMC未进GitHub)
├── evolution/               # 约束演化
│   ├── constraint_generator.py  rule_registry.py
├── effect_anchored/         # ★ L2 运行时(核心·真实执行)
│   ├── routing/             #  model_router(成本红线) / lao_router_server / cost_tracker
│   │                        #  task_classifier / task_decomposer / switch_audit
│   ├── evolution/           #  约束生成/适配器/经验提取/规则注册
│   ├── attestation/         #  decision_record(ADR审计) / protocol(经验确权)
│   ├── cognitive_engine.py  #  ★ L3 三层认知(0.4/0.35/0.25)
│   ├── consent_gate.py      #  四阶段授权门
│   ├── hallucination_gate.py#  幻觉拦截
│   └── feedback_bus.py      #  反馈总线
└── schema.py  cli.py        # schema 定义 / CLI
```

---

## 一、【上一轮 ChatGPT 意见 → 修复】标注

来源: 上一轮 ChatGPT 17 项优化建议（`docs/LAO-optimization-decision-council-20260812.md`）。

| ChatGPT意见 | 修复落地 | commit | 状态 |
|:--|:--|:--|:--:|
| **#1 Protocol/Impl/Policy 三分离** | 建 `protocol/ open/ private/` 三目录·协议稳定实现可迭代 | 613ca66 | ✅ |
| **#2 Policy版本+溯源+签名** | weights.json 补 policy_id/version/source/evidence/signature | (policy层) | ✅ |
| **#3 Agent Decision Record** | `attestation/decision_record.py`(ADR schema+账本+audit_report) | 3c81d0b | ✅ |
| **#4 TrustEvent Replay** | ADRLedger.replay() 轻量 Replay·TrustEvent+ADR 时间序 | 3c81d0b | ✅ |
| **#5 Policy Change Gate 分级** | decision_record PolicyChangeGate GREEN/YELLOW/RED | 3c81d0b | ✅ |
| **#6 消灭双轨** | 实轨=effect_anchored/evolution·死轨 lao/evolution 已标记 | (治理) | 🔶澄清 |
| **#7 L3改名 Ownership&Attestation** | 确权交易→归 Melody·L3=Ownership&Attestation | 613ca66 | ✅ |
| **#8 Effective Compute Value** | cost_tracker + router (反向算力议价) | 354de77 | 🔶部分 |
| **#9 Switch Cost** | Effective Cost 模型(切换+失败+验证+延迟) | 354de77 | 🔶部分 |
| **#11 ExperienceAsset Schema** | attestation/protocol.py 资产schema | (协议) | 🔶部分 |
| **#14 Stella独立审计接口** | decision_record.audit_report() 可独立审计 | 3c81d0b | ✅ |
| **#15-20 P2生态** | 协议版本化/企业适配/Provider网络/市场API/DID/Web5 | (设计) | 🔶部分 |

**→ 上轮 ChatGPT 意见修复：P0全采纳+落地，P1/P2 部分采纳进 v3.3。**

---

## 二、【我们自己的修复】标注（超出 ChatGPT 意见·主动/创始人令）

| 修复 | 内容 | commit | 触发 |
|:--|:--|:--|:--|
| **模型名校验/400根治** | model_router MODEL_POOL 无效模型名修复(400→200)·provider对齐 | (08-09) | Momo报bug+自有 |
| **成本红线真实启用** | route_with_budget budget 死参数→真实生效·每日预算·pro→flash降级 | 354de77 | Stella优化派发 |
| **缓存感知路由** | miss/hit 成本倍率·quality底线不可破 | 354de77 | 自有+ChatGPT#8-11 |
| **归因聚合+成本预警** | L1归因聚合回传·cron预警 | dfb70eb | Stella派发 |
| **P0⑥双轨消歧(三分语义)** | Constraint(What开源)+SelfHealingConstraint(How闭源)+Adapter边界 | b20a3e8/f12fb8d/733d64d | **创始人终审** |
| **lao-router真实接入** | OpenAI兼容成本代理(:8765)·9Agent共用·证据链日志 | 2d3846f | **创始人最高指令** |
| **ContextIntegrityProtocol** | 6对象EventSchema·ContextRisk 8指标·TrustEvent链 | b357655 | 创始人令·先协议后实现 |
| **AgentRuntimeIntegrityProtocol** | 6对象·Detect加进程/端口观测·RecoveryBudget | 50a9636 | 创始人令+Zeus异议 |

---

## 三、核心架构修复亮点（代码级）

### 1. 成本红线真的生效（原死参数→真实降级）
```python
# model_router.py route_with_budget: budget 超限 → 成本敏感层 pro→flash
SAFETY_GATE = {"heavy":0.85, "reasoning":0.85, "code":0.80}  # 质量底线不可破
def route_with_budget(self, task, budget):  # v2.2 budget真实生效
    return self.route(task, budget=budget)
```

### 2. 三分语义消歧（创始人终审·双轨死结解开）
```python
# Constraint = What·开源契约 | SelfHealingConstraint = How·闭源执行
class SelfHealingConstraint(ABC):  # 内部执行轨·自愈
# ConstraintAdapter: What→How 正式边界(schema翻译/severity映射/provenance/TrustEvent)
```

### 3. lao-router 真实降本代理（9 Agent 共用·证据链）
```
OpenClaw → lao-router(:8765) → route_with_budget → 真实DeepSeek → events.jsonl
```
冒烟实测: 真实转发 /v1/chat/completions → "OK" · cost ¥0.000390 · 证据链完整。

### 4. ADR 可审计决策账本
```python
AgentDecisionRecord: adr_id/agent/options/selected/reason/evidence_hash/attestation_id
PolicyChangeGate: GREEN/YELLOW/RED  # 红线三要素: 签名+evidence_hash+lead_evidence
```

---

## 四、给审核者的完整审阅对照

| 需求 | ChatGPT 意见修复 | 自有修复 |
|:--|:--|:--|
| 可审计 | ADR(#3) + Replay(#4) + PolicyGate(#5) + Stella接口(#14) | ADR账本+audit_report |
| 成本降本 | ECV/SwitchCost(#8/9) | 成本红线T1-T5+lao-router真实降本 |
| 架构清晰 | 三分离(#1) + 双轨(#6) + L3改名(#7) | P0⑥三分语义+Adapter边界 |
| 真实运行 | — | **lao-router真实接入(创始人最高指令)** |

---

*LAO v3.3 真实代码级架构修复审计材料 · 2026-08-13 · 完整闭源代码13,203行可审 · 标注上轮意见+自有修复全覆盖*
