/**
 * index.js — lao-dsh 插件入口
 *
 * Cordis 插件：name / inject / Config / apply。
 * 串联 router、cache、compress、budget、harvest、memory、telemetry 七大模块。
 *
 * §5.4 Config schema（schemastery / zod 风格）。
 * §8 默认关闭 + 一键回滚：enabled=false 为出厂值。
 * §5.2 能力→挂点映射：全部 hook 在 apply() 内通过 ctx.effect 注册。
 *
 * 红线 C5：移除 overlay insert 条目即完全复原，ctx.effect 卸载即释放。
 */
import { LaoRouterAdapter } from './router.js'
import { RequestCache } from './cache.js'
import { CognitiveCompressor } from './compress.js'
import { BudgetGate } from './budget.js'
import { ExperienceHarvester } from './harvest.js'
import { MemoryInjector } from './memory.js'
import { Telemetry } from './telemetry.js'

// ── Config schema（§5.4）──
export const Config = {
  type: 'object',
  properties: {
    enabled:   { type: 'boolean', default: false },
    route: {
      type: 'object',
      properties: {
        enabled:    { type: 'boolean', default: false },
        downstream: { type: 'array', items: { type: 'object' }, default: [] },
      },
    },
    cache: {
      type: 'object',
      properties: {
        enabled:    { type: 'boolean', default: false },
        ttlMs:      { type: 'number', default: 0 },
        maxEntries: { type: 'number', default: 500 },
      },
    },
    compress: {
      type: 'object',
      properties: {
        enabled:       { type: 'boolean', default: false },
        domains:       { type: 'object', default: {} },
        minTools:      { type: 'number', default: 6 },
        onUnknownTool: { type: 'string', enum: ['warn', 'throw'], default: 'warn' },
      },
    },
    budget: {
      type: 'object',
      properties: {
        enabled:        { type: 'boolean', default: false },
        perTurnCeiling: { type: 'number', default: 0 },
      },
    },
    harvest: {
      type: 'object',
      properties: {
        enabled:     { type: 'boolean', default: false },
        ralEndpoint: { type: 'string', default: '' },
      },
    },
    memory: {
      type: 'object',
      properties: {
        enabled:  { type: 'boolean', default: false },
        filePath: { type: 'string', default: '' },
      },
    },
    telemetry: {
      type: 'object',
      properties: {
        jsonlPath: { type: 'string', default: '' },
      },
    },
  },
}

// ── 插件定义 ──
export const name = 'lao-dsh'
export const inject = ['llm', 'tools', 'agent']

/**
 * apply(ctx, config) — 插件装载入口。
 *
 * 所有 hook 通过 ctx.effect 注册，卸载即释放（C5 回滚无残留）。
 * enabled=false 时跳过全部能力，等同透明。
 */
export function apply(ctx, config = {}) {
  const cfg = { ...config }
  if (!cfg.enabled) return  // C5：默认关闭，零副作用

  // ── 初始化遥测 ──
  const tel = new Telemetry()
  tel.configure(cfg.telemetry?.jsonlPath)
  tel.emit('plugin/load-start', { config: { ...cfg, route: { ...cfg.route, downstream: '[redacted]' } } })

  // ── 初始化各模块 ──
  const cacheMod = new RequestCache()
  const compressMod = new CognitiveCompressor()
  const budgetMod = new BudgetGate()
  const harvestMod = new ExperienceHarvester()
  const memoryMod = new MemoryInjector()

  // ── L1 路由 adapter ──
  let adapter = null
  if (cfg.route?.enabled) {
    adapter = new LaoRouterAdapter(cfg.route.downstream || [])
    // 注册 adapter（C6 不得静默降级 — 注册即写埋点）
    try {
      ctx.llm.registerAdapter(['lao'], adapter)
      tel.emit('router/registered', { providers: (cfg.route.downstream || []).map(d => d.name) })
    } catch (err) {
      tel.emit('router/register-error', { error: err.message })
      // C3：配置漂移不打挂 harness
    }
  }

  // ── L1 缓存配置 ──
  if (cfg.cache?.enabled) {
    cacheMod.configure(cfg.cache.ttlMs, cfg.cache.maxEntries)
  }

  // ── L1 认知压缩配置 ──
  if (cfg.compress?.enabled) {
    compressMod.configure(cfg.compress.domains, cfg.compress.minTools, cfg.compress.onUnknownTool)
  }

  // ── L1 预算闸配置 ──
  if (cfg.budget?.enabled) {
    budgetMod.configure(cfg.budget.perTurnCeiling)
  }

  // ── L2 经验萃取配置 ──
  if (cfg.harvest?.enabled) {
    harvestMod.configure(true, cfg.harvest.ralEndpoint)
  }

  // ── L2 记忆注入配置 ──
  if (cfg.memory?.enabled) {
    memoryMod.configure(true)
    if (cfg.memory.filePath) {
      memoryMod.loadFromFile(cfg.memory.filePath)
    }
  }

  // ── Hook 注册（全部 ctx.effect，卸载即释放）──

  // agent/session-start：认知压缩 + 记忆注入 + 预算重置
  ctx.effect('agent/session-start', (agent) => {
    tel.emit('hook/session-start', {})

    // 认知压缩：获取已注册工具名 → 意图分类 → restrict
    if (cfg.compress?.enabled && agent?.ctx?.tools) {
      try {
        const schemas = typeof agent.ctx.tools.schemas === 'function'
          ? agent.ctx.tools.schemas()
          : []
        const toolNames = schemas.map(s => s.name || s).filter(Boolean)
        const intent = compressMod.classifyIntent(agent.messages || [])
        compressMod.applyRestrict(agent.ctx, toolNames, intent)
      } catch (err) {
        tel.emit('compress/session-error', { error: err.message })
      }
    }

    // 记忆注入
    if (cfg.memory?.enabled && agent?.ctx?.systemPrompt) {
      memoryMod.injectAll(agent.ctx.systemPrompt)
    }

    // 预算重置
    if (cfg.budget?.enabled) {
      budgetMod.resetTurn()
    }
  })

  // tools/pre-execute：预算闸拦截
  ctx.effect('tools/pre-execute', (event) => {
    if (!cfg.budget?.enabled) return
    const toolName = event?.tool || event?.name || 'unknown'
    const estimatedCost = event?.estimatedCost || 0
    const result = budgetMod.preExecute(toolName, estimatedCost)
    if (result?.deny) {
      tel.emit('budget/denied', { tool: toolName, reason: result.deny.reason })
      return result  // {deny: {reason}} → 拦截
    }
  })

  // tools/result：经验萃取（只读捕获）
  ctx.effect('tools/result', (result) => {
    if (cfg.harvest?.enabled) {
      harvestMod.onToolResult(result)
    }
  })

  // agent/request-error：失败接管（返回 retry）
  ctx.effect('agent/request-error', (event) => {
    tel.emit('hook/request-error', { error: event?.error?.message || 'unknown' })
    // R2：恢复路径 — 返回 retry 让框架重试
    return { kind: 'retry' }
  })

  // system-prompt/assemble：记忆兜底注入
  ctx.effect('system-prompt/assemble', (assembly) => {
    if (cfg.memory?.enabled && assembly?.systemPrompt) {
      memoryMod.injectAll(assembly.systemPrompt)
    }
  })

  tel.emit('plugin/loaded', {
    modules: {
      route: cfg.route?.enabled || false,
      cache: cfg.cache?.enabled || false,
      compress: cfg.compress?.enabled || false,
      budget: cfg.budget?.enabled || false,
      harvest: cfg.harvest?.enabled || false,
      memory: cfg.memory?.enabled || false,
    },
  })
}

// ── 默认导出（Cordis 兼容）──
export default { name, inject, Config, apply }
