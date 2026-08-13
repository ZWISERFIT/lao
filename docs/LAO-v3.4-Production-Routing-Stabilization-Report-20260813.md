# LAO v3.4 Production Routing Stabilization Report

> DRI: Tristan · 2026-08-13 21:5x · Stable Rollout 模式（创始人令·非全量硬切）
> 目标: 恢复 lao-router 真实接入 + 防误回滚保护，不破坏 Release Gate

---

## 1. 为什么发生 revert（如能确认）

**现象**：19:42 完成 lao-router 接入（9 agent → 8765），20:49 openclaw.json 被改回直连 `https://api.deepseek.com/v1`。

**已确认**：
- openclaw.json 未入 git（无法比对历史）
- Agent-Bus 无回滚通告；Momo 21:21 消息是 07-30 旧审查（无关）
- **根因定性**：production routing state 无保护机制 → 任何进程/Agent 可静默修改 provider/baseUrl，无 before/after/actor/reason/approval 记录。

**创始人决策**：不调查具体是谁（避免不可控），直接补保护机制（RoutingStateGuard）。

---

## 2. 恢复过程（Stable Rollout·Canary 分批）

| 批次 | Agents | 结果 |
|:--|:--|:--|
| 批1 | tristan / nova / stella | ✅ 验证通过（flash/pro 都 pong·其余 6 直连）|
| 批2 | zeus / ethan / luna | ✅ 切换 |
| 批3 | baron / momo / shuyu | ✅ 切换 |
| 最终 | 9/9 | ✅ 全量经 lao-router |

每批后 gateway 重启 + 验证，**不硬切**。

---

## 3. Canary 结果

- ✅ gateway 健康（200）
- ✅ lao-router :8765 active（systemd 托管）
- ✅ flash + pro 模型路径都响应
- ✅ streaming + 非 stream 都正常
- ✅ 9/9 agent baseUrl → `http://127.0.0.1:8765/v1`

---

## 4. 9 Agent 最终状态

```
deepseek-tristan / nova / stella / zeus / ethan / luna /
baron / momo / shuyu  →  http://127.0.0.1:8765/v1  (lao-router)
```
- 9/9 agent 真实接入 lao-router
- 保留 deepseek 基础 provider 作 fallback

---

## 5. CostSavingsEvent 真实产生证明

```
首次对话(canary final verification):
  4层能力: Agent Online ✓ · Cost Active ✓ · Memory Active ✓ · Trust Active ✓
  成本节省: saved $0.00195 · ratio 66.7%
  事件: CostSavingsEvent (subtype=EconomicEvent) 已产生
```
真实调用链：OpenClaw Agent → lao-router(:8765) → Provider Capability → DeepSeek。

---

## 6. RoutingStateGuard 设计（防误回滚）

**问题**：production routing state 无保护 → 20:49 被静默改回。

**设计**（commit `2b6de97`）：
```
RoutingChangeEvent:
    before / after / changed_agents / timestamp / actor / reason / approval / checksum

RoutingStateGuard:
    snapshot()         # 提取 provider/baseUrl 映射
    save_snapshot()    # 保存当前状态
    detect_change()    # 比对 → 变化则产生 RoutingChangeEvent（append-only 持久化）
    verify_routing()   # 校验 9 agent 是否都指向预期 baseUrl
```

**核心原则**：**禁止静默改变** —— 任何 provider/baseUrl 修改必须产生 RoutingChangeEvent。

---

## 7. 不破坏 Release Gate（Phase 4）

| 组件 | 状态 |
|:--|:--:|
| Developer SDK / AgentRuntime | ✅ Stable |
| TrustEvent / Cost Intelligence | ✅ Stable |
| Founder Cognitive Policy / DID-VC / DWN / ExperienceAsset schema | ✅ 未动 |

## 8. 回归结果

| 项 | 结果 |
|:--|:--:|
| 全量测试 | **91 passed**（86→91·无回归）|
| RoutingStateGuard | 5 项测试通过 |
| 9 agent 路由 | 9/9 → lao-router |

---

## Commit
- `2b6de97` 🛡️ RoutingStateGuard 防误回滚保护（Phase3）
- （Phase2 canary 3 批 + 最终验证）

---

*LAO v3.4 Production Routing Stabilization Report · 2026-08-13 · Stable Rollout 完成*
