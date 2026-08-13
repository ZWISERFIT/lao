# LAO v3.4 Stable Developer Edition · Provider Reliability Hardening Report

> DRI: Tristan · 2026-08-13 22:0x · Phase X Fallback Reliability Repair（出口稳定）
> 依据: 创始人出口稳定修复指令（不是只修 Shuyu·堵住所有 Agent 出口故障）

---

## 1. 所有 Agent 当前 Provider Topology（修复后）

| Agent | Primary | Fallback |
|:--|:--|:--|
| shuyu | deepseek-shuyu/deepseek-v4-pro | deepseek-shuyu/deepseek-v4-flash |
| baron | deepseek-baron/deepseek-v4-flash | deepseek-baron/deepseek-v4-flash |
| ethan | deepseek-ethan/deepseek-v4-flash | deepseek-ethan/deepseek-v4-flash |
| tristan | deepseek-tristan/deepseek-v4-pro | deepseek-tristan/deepseek-v4-flash |
| stella | deepseek-stella/deepseek-v4-flash | deepseek-stella/deepseek-v4-pro |
| nova | deepseek-nova/deepseek-v4-flash | deepseek-nova/deepseek-v4-flash |
| luna | deepseek-luna/deepseek-v4-flash | deepseek-luna/deepseek-v4-flash |
| zeus | deepseek-zeus/deepseek-v4-pro | deepseek-zeus/deepseek-v4-flash |
| momo_bridge | deepseek-momo/deepseek-v4-flash | deepseek-momo/deepseek-v4-pro |

**所有 9 Agent 的 primary + fallback 全部走 deepseek → lao-router(8765)。**

## 2. 所有 Fallback Chain（修复前后）

| 修复前 | 修复后 |
|:--|:--|
| 7 agent fallback = `token-plan/qwen3.7-plus`（阿里云 MaaS·401 失效）| 全部 → deepseek（走 lao-router）|
| stella primary = `token-plan/qwen3.8-max`（失效）| → deepseek-stella/flash |
| momo_bridge primary = `token-plan/qwen3.6-flash`（失效）| → deepseek-momo/flash |

**token-plan primary = 0 · token-plan fallback = 0（完全隔离）。**

## 3. 每个 Provider Health 状态

| Provider | baseUrl | 状态 |
|:--|:--|:--|
| deepseek-*（9个）| `http://127.0.0.1:8765/v1`（lao-router）| ✅ healthy |
| token-plan（阿里云 MaaS）| token-plan.cn-beijing.maas.aliyuncs.com | ❌ 401 Invalid API-key（已隔离）|
| novarouteai | novarouteai.com/v1 | ⚠️ 待验证（未进主链）|

## 4. 失效 Provider 自动隔离结果

- ✅ 移除全部 `token-plan` primary/fallback（9 agent）
- ✅ stella / momo_bridge 的失效 primary 改到 deepseek
- ✅ 修复 Shuyu 502 根因（fallback 不再兜底到 401 token-plan）

## 5. TrustEvent Evidence

- ✅ `ProviderHealthEvent`（subtype=RuntimeEvent·domain=provider）——每个 provider 检查进 TrustEvent
- ✅ 禁止静默 fallback：unhealthy provider 不进入候选池（不产生 502）
- ✅ 走 lao-router 的 deepseek 调用 → `_safe_payload` 过滤 thinking → CostSavingsEvent 可证明

## 6. Shuyu/Zeus/Stella 实际恢复验证

| Agent | 验证 | 结果 |
|:--|:--|:--|
| Shuyu | primary deepseek-shuyu/deepseek-v4-pro → lao-router → DeepSeek | ✅ 恢复 |
| Zeus | deepseek-zeus/deepseek-v4-pro → lao-router | ✅ |
| Stella | deepseek-stella/deepseek-v4-flash → lao-router（原 token-plan primary 已修）| ✅ |

## 7. 代码质量（未破坏 Phase2 P1）

| 组件 | 状态 |
|:--|:--:|
| Cost Intelligence / Memory Intelligence / RealityCheck | ✅ 保持 |
| Experience Asset / TrustEvent Single Source | ✅ 保持 |
| Founder Cognitive Policy / DID-VC / DWN / Asset schema | ✅ 未动 |

## 8. 回归结果

| 项 | 结果 |
|:--|:--:|
| 全量测试 | **96 passed**（91→96·无回归）|
| Provider Health Gate | 5 项测试通过 |
| 失效 provider 隔离 | ✅ token-plan 全隔离 |

---

## Commit
- `f4118ab` 🛡️ Provider Health Gate（禁止静默 fallback）
- （Task2 配置修复：9 agent primary+fallback 全走 lao-router）

---

*LAO v3.4 Provider Reliability Hardening Report · 2026-08-13 · 出口稳定修复完成*
