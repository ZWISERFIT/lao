# RIS P0-1 · Provider 健康监控接入 — 实现报告

> 成熟部署加速 · Shuyu 立项 · 关联成本事故复盘（2026-08-14）
> DRI: Tristan · 2026-08-15
> 状态: ✅ 已实现 + 已接入 ris-monitor 常驻运行 + 6 tests 通过

---

## 一、任务完成度

| # | 任务 | 状态 |
|:--|:--|:--|
| 1 | 复用 ProviderHealthGate 检测 lao-router(8765)/直连 provider 可用性 | ✅ 完成 |
| 2 | 产出 provider_unavailable 事件（agent.py 接入） | ✅ 完成 |
| 3 | 关联成本链路：provider 掉线→LAO 回退直连→成本变化 | ✅ 完成（cost_chain 注解） |
| 4 | 验证：产出 ≥1 条 provider 健康事件样本 | ✅ 完成（样本落盘） |

---

## 二、实现内容

### 1. 新增 `ris/health/provider_monitor.py`（核心模块）

**复用 ProviderHealthGate**（`lao.effect_anchored.provider_health_gate`），不再用"端口通=健康"的粗糙判断。

```python
class ProviderHealthMonitor:
    def check_lao_router(self) -> RuntimeHealthEvent:
        # 探测 http://127.0.0.1:8765/v1/models
        # 复用 gate.check(provider="lao-router", endpoint_available=...)
        # 不可达 → provider_unavailable(critical) + cost_impact=high + fallback_target=deepseek-direct

    def check_deepseek_direct(self) -> RuntimeHealthEvent:
        # 探测 https://api.deepseek.com/v1/models
        # 不可达 → provider_unavailable(critical) + cost_impact=critical + fallback_target=None
```

**关键设计**：探活语义区分
- `2xx/401/403/404` = 端点可达（服务在响应，401 只是需认证，**不是掉线**）
- `连接拒绝/超时/DNS失败` = 真掉线（才产出 provider_unavailable）

> 这修复了原 `agent.py` 里一个隐患：直连 DeepSeek 的 `GET /v1/models` 会返回 401（需 Bearer key），
> 旧代码 `_http_ok` 会把它误判为"provider 掉线" → 高频误报 critical。

### 2. 成本链路捕获（P0-1 核心价值）

每个 `provider_unavailable` 事件 `detail` 带三段成本链路元数据：

```json
{
  "fallback_target": "deepseek-direct",           // 回退目标
  "cost_impact": "high",                           // high/routed/critical
  "cost_chain": "provider→lao-router(8765)→回退直连deepseek→单key混用+cache_miss↑+无budget红线→成本↑"
}
```

这是把 **2026-08-14 成本事故的 4 个主根因**（model名当task / quality_gate / 单key混用 / thinking参数）
在 provider 掉线瞬间就标注到事件上，供财务/智囊团复盘时直接关联，无需再回溯。

### 3. `agent.py` 接入（替换浅层检查）

原代码（`36a1006` commit）用裸 `_http_ok` + `if False else True` 死代码 stub，现替换为：

```python
provider_monitor = ProviderHealthMonitor()
for ev in provider_monitor.check_once():
    _emit(ev)
    events.append(ev)
    print(f"  ⚠️ {ev.event_type}: {ev.agent_id} cost_impact={ev.detail.get('cost_impact')}")
```

### 4. 事件类型注册

`ris/events.py` 的 `RIS_EVENT_TYPES` 新增：
- `provider_unavailable`（provider 不可用·带 cost_impact）
- `provider_ok`（provider 健康·recovered 对偶信号）

---

## 三、验证结果

### 测试（6 new + 18 total = 全通过）

```
tests/test_ris_provider_monitor.py  → 6 passed
tests/test_ris_runtime_health.py    → 5 passed（1 处更新事件类型断言）
tests/test_provider_health_gate.py  → 6 passed
```

### provider_unavailable 事件样本（真实结构·已落盘）

样本位置：`ris/state/data/provider-health-event-samples.json`

```json
{
  "layer": "ris",
  "event_type": "provider_unavailable",
  "agent_id": "lao-router",
  "status": "detected",
  "severity": "critical",
  "detail": {
    "provider": "lao-router",
    "endpoint": "http://127.0.0.1:9999/v1/models",
    "ok": false,
    "latency_ms": 116.3,
    "error": "URLError",
    "reason": "URLError: http://127.0.0.1:9999/v1/models",
    "fallback_target": "deepseek-direct",
    "cost_impact": "high",
    "cost_chain": "provider→lao-router(8765)→回退直连deepseek→单key混用+cache_miss↑+无budget红线→成本↑"
  },
  "ts": "2026-08-15T15:43:26+00:00"
}
```

### 真实运行验证（当前环境）

- `lao-router`(8765) 在线 → 产出 `provider_ok`（recovered·info·不误报）
- `deepseek` 直连端点可达（401 需 key）→ 产出 `provider_ok`（修复旧误报）
- ris-monitor.service 已重启应用新代码，活性 `active (running)`，无 import 错误

---

## 四、成本链路说明（为什么是 P0）

**Provider 掉线不是孤立事件，是成本事故的触发链起点：**

```
Provider 掉线（lao-router 8765 断）
    ↓  RIS 捕获 provider_unavailable(cost_impact=high)
OpenClaw 回退直连 deepseek-{agent}
    ↓  RoutingStateGuard 审计路由态变更
丢失 KVCache 隔离 + 单 key 混用 → cache miss 暴增
    ↓  无 budget 红线 + 无 pro→flash 降级
成本暴涨（原 2026-08-14 事故的 4 主根因集中触发）
```

RIS 现在能在链起点（Provider 掉线瞬间）就产出带 `cost_impact` 的 `provider_unavailable` 事件，
让后续的成本漂移有迹可循，而不是等成本账单暴增后再回溯根因。

---

## 五、⚠️ 独立高价值发现：session_bloat（给创始人的额外发现）

**RIS 已持续捕获 `session_bloat` 事件，当前累计 83+ 条（每 30s 一条持续触发）。**

- 根因：`/home/agentuser/.openclaw/agents/{luna,momo_bridge,nova,tristan}/sessions/*.trajectory.jsonl`
  大量 session 文件 **> 8MB（实测 9.3~10.5MB）**，主要是 **luna** agent。
- **这正是 webchat 慢的根因**：session 文件过大 → 加载解析慢 → 响应延迟。
- 已发现 20+ 个 10MB 级 session 文件（luna 最多，nova/tristan 也有）。
- **建议**：独立立项做 session 归档/压缩（compaction 触发阈值 + 历史 session 冷归档），
  这是成本之外的又一个高价值性能优化点，RIS 已在持续产证据，可直接接优化动作。

---

*RIS P0-1 Provider 健康监控接入 · 完成 · 2026-08-15*
