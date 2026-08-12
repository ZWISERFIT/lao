# ContextIntegrityProtocol RFC (v1.0)

> 创始人终审(2026-08-13 06:40 · Zeus三项异议采纳) · 先协议后实现 · 一页
> DRI: Tristan · Stella独立审计 · 创始人终审
> 性质: 协议定义(非实现)· 目标=把 Context 生命周期变成可观测/可验证/可审计的 Trust Events

## 1. 协议边界（三分离）

| 层 | 范围 | 归属 |
|:---|:---|:---|
| **Protocol** | 什么是 ContextRisk / 记录什么字段 / 验证什么事件 / provenance / 审计 / replay | **LAO 开源** |
| **Reference Implementation** | 具体 Adapter 实现（OpenClaw 第一个） | 可用（开源） |
| **Risk-Recovery-Optimization Policy** | Risk 权重 / 阈值 / promotion threshold / recovery strategy / auto-fix / compaction policy / provider-model tradeoff / Founder Cognitive Policy | **ZWISERFIT private** |

**边界铁律（创始人）：** LAO **不把指标写死成"根因"**。流程 = `记录事实 → 建立相关性 → 验证因果 → 形成 Experience/Policy`（符合 Trust 原则）。

## 2. Event Schema（6 对象）

### ContextBudget
```
{ budget_id, session_id, max_context_tokens, warning_threshold, created_at }
```
### ContextRisk（第一版不预设固定权重·先观察 8 指标）
```
{ risk_id, session_id, ts,
  bootstrap_size,              # 大型 Bootstrap 体积
  bootstrap_truncation,        # 被截断比例
  compaction_frequency,        # Compaction 次数/频率
  bootstrap_reinjection_cost,  # 再注入成本(重复 Context Construction)
  context_build_latency,       # 上下文构建时延
  model_request_latency,       # 模型请求时延
  execution_stall,             # 执行卡顿
  recovery_success }           # 恢复成功
# 权重组合 → 私有 Policy 决定(不在 Protocol 写死)
```
### BootstrapEvent
```
{ event_id, session_id, ts, size_tokens, truncated_tokens, source }
```
### CompactionEvent
```
{ event_id, session_id, ts, before_tokens, after_tokens, triggered_by }
```
### ContextDecision
```
{ decision_id, session_id, ts, context_strategy, model_selection,
  provider, fallback_chain, budget_ref, risk_ref }
```
### ContextRecoveryEvent
```
{ event_id, session_id, ts, stall_ref, recovered, recovery_latency_ms }
```

## 3. Adapter Interface（OpenClaw 第一个 Adapter 怎么接）

```
任何 Agent Runtime (OpenClaw/Hermes/LangGraph/AutoGen)
        │  实现 RuntimeObsAdapter
        ▼
ContextIntegrityAdapter
   ├─ capture_bootstrap(session)        → BootstrapEvent
   ├─ capture_compaction(session)       → CompactionEvent
   ├─ compute_context_risk(events)      → ContextRisk(8字段·不设权重)
   ├─ record_decision(decision)         → ContextDecision
   ├─ record_recovery(recovery)         → ContextRecoveryEvent
   └─ emit_trust_events(events)         → TrustEventLedger(可验证)
```

- OpenClaw 作为第一个 Adapter 实现 `RuntimeObsAdapter`。
- LAO L1 只管: Context strategy / Model selection / Provider / Fallback / Retry / Verification path / Execution risk。
- **不进入**: Agent 组织调度 / Department scheduling / Board governance / Company 资源分配。

## 4. TrustEvent 链（每步可验证的 provenance 链路）

```
BootstrapEvent ──┐                            (每次事件带 session_id + ts + event hash)
CompactionEvent ─┼──→ ContextRisk ──→ ContextDecision ──→ ContextRecoveryEvent
                 │       │                   │                    │
                 │       └── risk_id         └── budget_ref        └── stall_ref
                 └─────────────── 全部 emit 到 TrustEventLedger(append-only·dedup·hash·verify)
```

**provenance 原则（Trust 原则）：** 每步事件携带 `引用链`（decision→risk→bootstrap/compaction），
使审计者可独立验证"决策是否由真实 context 生命周期事件驱动"，不被人类/Agent 篡改。

## 5. 克制约束（创始人要求）

- 不扩成 "Agent Runtime OS"。
- ContextIntegrityProtocol 目标 = 任何 Agent Runtime 把 Context 生命周期变可观测/可验证/可审计 Trust Events。
- OpenClaw 只是第一个 Adapter（未来可接 Hermes/LangGraph/AutoGen）。
- LAO 不负责任何 Runtime Scheduler。

---
*ContextIntegrityProtocol RFC v1.0 · 2026-08-13 · 待 Stella 独立审计 + 创始人终审*
