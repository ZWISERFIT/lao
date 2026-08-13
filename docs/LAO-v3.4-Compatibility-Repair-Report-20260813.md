# LAO v3.4 Stable Developer Edition · Compatibility Repair Report

> DRI: Tristan · 2026-08-13 21:0x · Stabilization Mode（不新增架构·只修兼容）
> 依据: 创始人 Emergency Repair Instruction（OpenAI Compatible 参数协议不兼容）

---

## 1. Root Cause（根因）

**现象**：带 `thinking` 参数的请求失败：
```
Completions.create() got an unexpected keyword argument 'thinking'
```

**根因**：`lao_router_server.py` 中
```python
payload = {**body, "model": chosen_model}
resp = client.chat.completions.create(**payload)
```
**全量透传** OpenClaw/Developer 请求的所有参数（含 `thinking`、未知参数）→ 直接 `**payload` 传给 OpenAI SDK → SDK 收到不支持的 `thinking` → TypeError。

---

## 2. Modified Files（修改文件）

| 文件 | 改动 |
|:--|:--|
| `lao/effect_anchored/routing/lao_router_server.py` | +SUPPORTED_PARAMS 白名单 + ProviderCapabilityRegistry + `_safe_payload()` 参数过滤/能力协商 + CapabilityFallbackEvent |
| `tests/test_compat_router_params.py` | 新增 5 项参数过滤/能力协商测试 |

---

## 3. Fix（修复内容·Phase A/B）

### A. 参数白名单过滤（禁止透传未知参数）
```python
SUPPORTED_PARAMS = {"model","messages","temperature","max_tokens","stream","tools","response_format",...}
payload = {k:v for k,v in body.items() if k in SUPPORTED_PARAMS}
```
发送前过滤 → 未知参数(thinking/自定义)不会进入 `Completions.create()`。

### B. Provider Capability Detection（能力协商）
```python
ProviderCapabilityRegistry = {
  "deepseek-v4-flash": {"thinking": False, "reasoning_content": True, "tools": True, "stream": True},
  "deepseek-v4-pro":   {"thinking": False, "reasoning_content": True, "tools": True, "stream": True},
}
```
Body 含 `thinking` 但 provider 不支持 → **自动 drop + CapabilityFallbackEvent**（入 TrustEvent 链·不报错）。

---

## 4. Tests Added（新增测试）

`tests/test_compat_router_params.py`（5 项）：
1. `thinking` 参数被 drop + CapabilityFallbackEvent
2. 未知参数被白名单过滤
3. 支持参数(stream/max_tokens/tools)保留
4. 能力注册表(thinking=False/stream=True)
5. 白名单定义了核心参数

---

## 5. Regression Result（回归结果）

| 项 | 结果 |
|:--|:--:|
| 全量测试 | **81 passed**（76 → 81·+5 新增·无回归）|
| thinking 请求 | ✅ 不再报错（返回 hello 响应）|
| streaming | ✅ SSE 正常 |
| 非stream + 未知参数 | ✅ 过滤成功|
| CapabilityFallbackEvent | ✅ 已入证据链 |

---

## 6. External Developer Experience Verification（测试通过）

- **Phase D Test1**（thinking=off 请求）→ ✅ 返回正常响应 + CapabilityFallbackEvent
- **Phase D Test2**（OpenClaw 真实路径 Developer→Gateway→LAO→DeepSeek）→ ✅ 对话/streaming/session/memory 正常
- **Phase D Test3**（成本价值）→ ✅ CostSavingsEvent 保持（未破坏）

---

## 7. 五维价值保持（Phase C·未破坏）

| 价值 | 事件 | 状态 |
|:--|:--|:--:|
| Cost Intelligence | CostSavingsEvent/EconomicEvent | ✅ 保持 |
| Memory Intelligence | MemoryOptimizationEvent/MemoryEvent | ✅ 保持 |
| RealityCheck | AnswerConfidenceEvent/EvidenceEvent | ✅ 保持 |
| Experience Asset | ExperienceAsset/Attestation | ✅ 保持 |
| Trust Infrastructure | TrustEvent single source | ✅ 保持 |

## 8. 禁止修改项（Phase E·未触碰）

✅ 未改 Founder Cognitive Policy · 未新增 Governance · 未开放 DWN · 未改 DID/VC · 未删 TrustEvent · 未绕过 Evidence Verification

---

## Commit
- `817e469` fix(router): add provider capability negotiation and safe parameter filtering
- `86bf53e` test(router): parameter filtering & capability negotiation tests (Phase D)

---
*LAO v3.4 Compatibility Repair Report · 2026-08-13 · Stabilization Mode · DRI Tristan*
