# LAO v3.4 Pioneer Stable Architecture · Phase2/Phase3 Roadmap 拆解

> 创始人施工任务书 2026-08-13 18:20 · DRI: Tristan
> 战略: Trust Layer 代码库 → **AI Agent Trust Infrastructure（外部开发者可体验版）**
> 顺序: 完整闭源修复 → 内部稳定验证 → 外部体验版 → 开源边界 → 生态开放
> 禁止: 为了星标提前暴露未稳定模块 / 为 Demo 删可信机制 / 将 private 下沉公开

---

## 〇、核心战略转向（创始人）

**从"加模块"→"闭环开发"：每个能力必须进 6 阶段闭环**
```
Detect → Evidence → Decision → Action → Verification → Experience
```

**最终目标（竞争内核）：**
```
AI 发生错误 → AI 发现错误 → AI 修复错误 → AI 证明修复 → AI 记住经验 → AI 变得更可靠
```
这个闭环成立 → LAO 从 Agent Framework 进入 **AI Native Operating Infrastructure**。

---

## 一、Phase2 P0 五项（外部体验基础·按优先级）

### 🔴 P0-1 Agent Runtime Registry（最高优先·先做）
**问题**：用户不知道 Agent 是否活着。
**交付**：`RuntimeRegistry`
```python
@dataclass AgentRuntimeState:
    agent_id, did, status(online/recovering/offline), health,
    cpu, memory, context_usage, last_success, last_failure,
    current_model, provider, recovery_state
```
**外部体验**：
```
Stella  ✓ online  DeepSeek v4 flash  Latency 2.1s  Trust 98
Zeus    ⚠ recovering  Failure domain: gateway  Recovery 1/3
```
**闭环**：Register(Detect) → StatusEvent(Evidence) → Decision → Action → Verify(Verification) → Experience

### 🔴 P0-2 ContextIntegrityProtocol 完成
**问题**：Context 监控 → **Context Lifecycle Management**。
**事件**：ContextEvent / BootstrapEvent / CompactionEvent / RecoveryEvent / ContextRiskEvent
**指标**：bootstrap_cost / memory_injection_size / compaction_frequency / context_growth_rate / token_efficiency / cpu_pressure / latency_pressure
**Risk 三层**：Open Protocol(ContextObservation/RiskSchema/EventSchema·公开) + Private Intelligence(RiskScore/阈值/auto-mitigation·闭源)
**⚠️ 禁止第三套权重** → 必须调用 `FounderCognitivePolicy`（统一认知权重）

### 🔴 P0-3 ExperienceAsset MVP（提前·不等 Phase3）
**问题**：外部开发者贡献缺"资产感"。
**交付**：ExperienceAsset = Asset ID + Creator DID + Problem + Solution + Verification% + Attestation(TrustEvent hash)
→ Web5 原住民入口

### 🔴 P0-4 Recovery Experience Replay（从"能恢复"→"能学习"）
**交付**：RecoveryMemory + SimilarityMatch + PreviousSolution + SuccessProbability
**流程**：Failure → Search Experience → Recommend Recovery → Verify → Update Experience
（衔接 Phase1 Step3 RecoveryVerifier）

### 🔴 P0-5 External Developer Sandbox
**交付**：LAO Sandbox Runtime（Agent模拟 + Failure注入 + Recovery测试 + TrustEvent查看 + Experience生成）
**目标**：开发者第一天"故意弄坏 Agent，看 LAO 自动修"——杀手级体验

---

## 二、Phase3 提前布局（不提前开放）

### DID（立即准备）
每个 Developer/Agent/Organization 拥有 DID Identity

### VC（贡献证明）
ZWISERFIT Early Native Developer / LAO Contributor / Recovery Pattern Creator

### DWN（暂不进内核）
保持：LAO → DWN Adapter → User Data Sovereignty

---

## 三、开源边界重新定义（明确）

| 最终开源 | 保持私有 |
|:--|:--|
| protocol/ schema/ TrustEvent | private/ Founder Cognitive Policy |
| Adapter Interface Developer SDK | Routing Strategy Recovery Strategy |
| | Experience Ranking Economic Intelligence |

---

## 四、代码质量要求（v3.4 硬性）

1. **单一事实源**：禁止多 Ledger/Registry/Event → **TrustEvent 唯一事实**
2. **所有自动动作可证明**：restart/switch/fallback 必须产生 Action+Evidence+Verification+Attestation
3. **所有恢复有终点**：attempt≤budget + failure escalation + human approval（衔接 Phase1 Step5）

---

## 五、验收标准（不以代码量验收·以外体验收）

| Test | 新人体验 | 时间 |
|:--|:--|:--|
| Test1 | 安装+创建身份+启动 Agent | 30 分钟 |
| Test2 | 故意制造故障 → LAO 检测/定位/修复/证明 | 30 分钟 |
| Test3 | 贡献一次优化 → 自动生成 ExperienceAsset | — |
| Test4 | 第二次同类故障 → LAO 调用历史经验 | — |

---

## 六、施工原则（每项硬性）

- ✅ 每项一个 commit
- ✅ 每项必须测试
- ✅ 每项必须产生 TrustEvent
- ✅ 每项必须可审计

---

## 🎯 建议施工顺序（闭环优先·P0-1 先做）

```
Phase2-P0-1 RuntimeRegistry        ← 最先（体验第一印象·最高优先）
Phase2-P0-5 Sandbox                 ← 其次（外部开发者载体）
Phase2-P0-3 ExperienceAsset MVP     ← 第三（资产感/感知价值）
Phase2-P0-4 Recovery Experience     ← 第四（能学习）
Phase2-P0-2 ContextLifecycle        ← 第五（真实事故治理）
Phase3 布局: DID → VC（不开放）
```

> 每项以 6 阶段闭环串接, 而非孤立模块; 先完整闭源内测, 再设计开源版。

---
*LAO v3.4 Phase2/Phase3 Roadmap 拆解 v1.0 · 2026-08-13 · DRI Tristan · 待创始人确认后施工*
