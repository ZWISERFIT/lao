# LAO v3.4 QODER 修复总结

**修复日期**: 2026-08-16
**依据**: `docs/QODER-LAO-v3.4-bug-audit-20260816.md`(QODER 深度 Bug 审计)
**修复人**: Qoder
**测试结果**: `python3 -m pytest tests/ -q` → **153 passed**(含 `test_strip_reasoning_toolcalls.py` 4 passed)· 0 失败 0 回归

---

## 修改文件

| 文件 | 改动 |
|------|------|
| `lao/effect_anchored/routing/lao_router_server.py` | C1/C2/C3/C4/M1/M2/M3/M5/M6/m1/m3 |
| `lao/effect_anchored/routing/cost_intelligence.py` | M4(model_cost 支持 hit/miss 价差)+ M6(compute_saving 透传缓存) |
| `lao/effect_anchored/routing/model_router.py` | 无需改动(C4 在转发层统一) |
| `lao/effect_anchored/routing/hit_rate_aggregator.py` / `ab_test_six_metrics.py` / `cost_tracker.py` | 无需改动(事件字段在写入端已修复·消费端天然兼容) |

---

## P0 修复

### C1 + M2 · 转发层尊重 `chosen_model`(成本红线回归)
- `_safe_payload` 删除 `preserve_requested` 参数与 requested_model 回填逻辑,改为**无条件 `payload["model"] = chosen_model`**。
- 根因: 旧逻辑把转发 model 设回请求方 model → `route_with_budget` 的 pro→flash 预算降级被架空(决策 flash·实际按 pro 计费·¥400 事故)。
- 缓存稳定与转发 model 解耦,由三层保障: ① provider/api_key 按 agent 固定(C4);② `payload["user"]` 按 agent 隔离;③ `_stabilize_messages` 稳定前缀。
- M2: 所有事件日志新增 `forwarded_model` 字段,与 `chosen_model` 可核对,监控与真实计费不再脱节。
- 顺带删除随之失效的死代码 `_is_valid_model_name`(m2 随之根治: 转发 model 恒为 MODEL_POOL 内合法 model)。

### C2 + M1 + M5 · 流式成本计入每日预算 + 失败必落日志 + 真实 cost_yuan
- 新增统一结算入口 `_settle_and_log()`: 流式/非流式共用(计费/记账/节省/日志同一代码路径)。
- `_sse_gen` 重构为 `try/except/finally`,**在 finally 中**结算: 成功、异常、客户端断开(GeneratorExit)三种路径都必然执行。
- finally 中按 `stream_usage`(input/output/hit/miss)计算真实成本并调用 `_record_cost(cost_usd)` → 预算红线首次覆盖 OpenClaw 默认的流式主流量。
- 事件日志新增 `status`("ok"/"error")与 `error` 字段;流式 400/500/网络错误不再是日志黑洞,A/B `success_rate` 不再被高估(M1)。
- 流式 `cost_yuan` 不再硬编码 0.0,按真实 usage 计算(M5)。

### C3 · async 端点不阻塞事件循环
- `client.chat.completions.create(**payload)` 改为 `await asyncio.to_thread(...)`(C3)。
- async 上下文中的 `_log_event` 同步文件写同样经 `await asyncio.to_thread(...)` 执行(错误路径与非流式结算路径);流式生成器本身由 Starlette 在线程池迭代,保持同步写。

### C4 · provider 绑定与缓存隔离统一
- 旧条件 `req_provider in PROVIDER_CONFIG and not (agent and ...)` 删除,改为:
  - **agent 一旦识别 → provider 固定为其绑定值**(baron/ethan/momo→token-plan·其余默认 deepseek,由 `route()` 过滤池保证),不再按请求前缀切换;
  - **仅无 agent 的裸请求**尊重 provider 前缀(deepseek/ token-plan/ novarouteai/)。
- 效果: 同一 agent 的 provider/api_key 恒定 → DeepSeek 前缀缓存不再因 key 切换而全量失效。
- `AGENT_PROVIDER_BINDING` 在 server 中已无引用,导入同步移除。

## P1 修复

### M4 · 成本计算引入 cache hit/miss 价差
- `lao_router_server.py` 新增 `MODEL_PRICING_YUAN`(¥/1M: pro hit 0.6 / miss 3.0 / out 6.0;flash hit 0.1 / miss 1.0 / out 2.0)与 `_compute_cost_yuan(model, hit, miss, out)`;miss 档与旧固定价一致(无缓存请求成本不变),hit 档按官方价差折算。
- `cost_intelligence.py`: `MODEL_BASELINE_COST` 每档新增 `input_cache_hit` 价;`model_cost()` 新增 `cache_hit`/`cache_miss` 参数(缺省=全 miss 档·与旧行为完全兼容);`compute_saving()` 透传缓存参数,基线 original_cost 仍全按 miss 档(保守口径)。

### M6 · SavingsEngine 接入主链路
- `lao_router_server.py` 挂载模块级 `savings_engine = SavingsEngine()`,每次响应(流式/非流式·含降级)在 `_settle_and_log` 中调用 `compute_saving(agent, tier, baseline_model, chosen_model, in_tok, out_tok, cache_hit, cache_miss, quality_score, switch_reason)`。
- `quality_score` 从 `MODEL_POOL` 查所选模型真实质量分;`switch_reason` 区分 `tier_match` / `budget_redline_degrade`。
- 新增 **`GET /v1/savings`** 端点返回 `impact_report()`(requests/original/optimized/saved/efficiency/quality),供 Nova/Stella/Dashboard 消费;事件日志同步落 `saving_usd`。

## P2 修复

- **m3** 请求入口生成 `request_id`(优先透传 `x-request-id` header,否则 `uuid4().hex[:12]`),贯穿该请求的全部日志事件(转发错误/流式/非流式)。
- **m1** 删除第二个不可达的 `return StreamingResponse(...)`。
- **M3** `_log_event` 写入前将空 `agent` 归一为 `"unknown"`(归因/聚合不再出现空串;`_extract_agent` 返回值语义不变,既有测试兼容)。

## 验证

1. **单元/回归**: `python3 -m pytest tests/ -q` → **153 passed**(关键回归集: `test_strip_reasoning_toolcalls.py` 4 passed、`test_compat_router_params.py`、`test_p11_cost_intelligence.py`、`test_b_agent_provider_binding.py`、`test_p1_agent_key_distribution.py` 全绿)。
2. **端到端冒烟**(TestClient + 伪造 provider client,事件日志重定向到临时文件):
   - ① 预算近枯竭 + light tier + 请求 pro → 实际转发 `deepseek-v4-flash`,事件 `degraded=true`、`forwarded_model=flash`(C1/M2 ✅)
   - ② 流式请求结束后 `_daily_cost` 真实增加、`cost_yuan>0`(C2/M5 ✅)
   - ③ baron 带 `deepseek/` 前缀请求 → provider 恒为 `token-plan`;无 agent 裸请求 → 尊重 `novarouteai/` 前缀;`agent=unknown` 归一(C4/M3 ✅)
   - ④ `GET /v1/savings` 返回真实 Impact Report(saved>0·efficiency 58.5%)(M6 ✅)
   - ⑤ 流式中途抛 `upstream 500` → 事件日志 `status=error` 且含 error 与 request_id(M1/m3 ✅)

## 设计语义保持

- LAO 强制路由 `chosen_model` 是设计核心,本次修复正是让转发层真正执行该决策(requested_model 仅作为 `requested_model`/基线字段记录,不再影响转发)。
- 降级白名单(`BUDGET_DEGRADABLE_TIERS`)、安全门禁、agent provider 绑定、9-Agent 独立 key 分发等既有行为全部保留。

---

*修复完成。审计报告 P0×4 + P1×4 + P2×3 全部落地,153 项测试零回归。*
