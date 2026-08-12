# AgentRuntimeIntegrityProtocol RFC (v0.1)

> 创始人令(2026-08-13) · ChatGPT Detect→Diagnose→Recover→Verify→Learn 五步 + Zeus 关键异议(进程/端口观测维度)
> DRI: Tristan · Stella独立审计 · 创始人终审
> 性质: 协议定义(非实现)·一页 · 把 Agent Runtime 故障周期变可观测/可验证/可审计 Trust Events

## 1. 协议边界（沿用 ContextIntegrityProtocol 三分语义铁律）

| 层 | 范围 | 归属 |
|:---|:---|:---|
| **Protocol(开源)** | RuntimeEvent / FailureSignal / Diagnosis **结构定义** · 可验证可审计 · replay | **LAO 开源** |
| **Reference Implementation** | OpenClaw 第一个 Adapter | 可用 |
| **Recovery Policy(闭源)** | 何时 restart/fallback/rebuild · Recovery Budget · 风险权重 · 升级 Board 阈值 | **ZWISERFIT private** |

**边界铁律：**
- 公开「怎么证明」(事件结构/诊断/审计/replay)，闭源「何时怎么自愈」(Recovery Policy)
- **Recovery Budget 必须有**：超预算 → SAFE MODE → Stella Audit → Board Alert，**禁止无限重试**
- OpenClaw 只是第一个 Adapter（可接 Hermes/LangGraph/AutoGen），不做 OpenClaw 专用修复脚本
- **Correlated Failure Detection**：多 Agent 同时异常 → 优先找共同依赖(gateway/network/provider)，不逐个乱重启

## 2. Event Schema（6 对象）

### RuntimeEvent（运行时事件·含 Zeus 新增进程/端口观测）
```
{ event_id, ts, layer, signal_type, detail, ref_session, source_agent }
layer: process | runtime | model_provider | tool
signal_type: process_duplicate | port_conflict | systemd_state |
             gateway_heartbeat | websocket_state | session_state |
             agent_heartbeat | timeout | fallback
```
⚠️ **Zeus 关键异议落地**：Detect 层必须感知「重复进程/端口冲突/systemd 状态」，不只观察 health/connection。

### FailureSignal（失败信号·聚合）
```
{ failure_id, ts, correlated_agents[], common_dependency, symptom, layer }
# Correlated: 多 Agent 同信号 → common_dependency=gateway/network/provider(优先查共同依赖)
```

### Diagnosis（诊断·记录事实→相关性→因果·不预设固定权重）
```
{ diagnosis_id, ts, failure_ref, root_cause_candidate[], confidence[],
  evidence_chain[], status }   # 证据链可验证; 权重由私有Policy定
```

### RecoveryAction(恢复动作)
```
{ action_id, ts, type, target, budget_consumed_ms }
type: restart | fallback | rebuild | safe_mode | escalate
```

### RecoveryResult（恢复结果）
```
{ action_id, ts, success, latency_ms, residual_risk }
```

### RecoveryAttestation（恢复可信证明·公开可验证）
```
{ result_id, ts, recovered, fingerprint, provenance[], audit_ready }
# 恢复结果可被 Stella/审计者独立验证(不靠自报)
```

## 3. Adapter Interface（OpenClaw 第一个 Adapter 怎么接）

```
RuntimeObservation
   ├─ process_layer:   detect_duplicate_processes / detect_port_conflict / systemd_service_state
   ├─ runtime_layer:   gateway_heartbeat / websocket_state / session_state / agent_heartbeat
   └─ model_tool_layer: timeout / fallback / provider_auth
        │
        ▼  emit
   RuntimeEvent → FailureSignal(correlated) → Diagnosis → RecoveryAction → RecoveryResult → Attestation
        │
        ▼
   TrustEventLedger(append-only·dedup·hash·verify·可审计)
```

- OpenClaw 作为第一个 Adapter 实现 `RuntimeObservation` 三观测层。
- **Recovery Budget 硬约束**：累积 recovery 耗时不超预算 → 超则 SAFE MODE + Stella Audit + Board Alert。

## 4. TrustEvent 链（每步可验证 provenance）

```
RuntimeEvent ─→ FailureSignal ─→ Diagnosis ─→ RecoveryAction ─→ RecoveryResult ─→ RecoveryAttestation
    │                │              │              │                  │                │
    ├── event_id     ├── failure_id ├── diagnosis_id├── action_id      ├── result_id    └── fingerprint+provenance(公开可验证)
    └── layer+signal └── correlated └── evidence_chain(可追溯)
               全链 emit 到 TrustEventLedger(append-only·hash·audit_ready)
```

**provenance 原则（Trust 原则）：** 每步携带引用链（attestation→result→action→diagnosis→signal→event），
审计者可独立验证"恢复是否由真实事件驱动"，不被篡改。

## 5. 克制约束（创始人/Zeus 要求）

- 不扩成 "Agent Runtime OS" / 不做 OpenClaw 专用修复脚本。
- 目标 = 任何 Agent Runtime 把故障周期变可观测/可验证/可审计 Trust Events。
- LAO 不负责任何 Runtime Scheduler。

---
*AgentRuntimeIntegrityProtocol RFC v0.1 · 2026-08-13 · 待 Stella 独立审计 + 创始人终审*
