/**
 * compress.js — L2 认知压缩：意图分类 → 工具域 → restrict
 *
 * 主机制：agent/session-start → agent.ctx.tools.restrict({allow})
 * 辅助：system-prompt/assemble 增补/兜底
 *
 * 红线 C2：工具裁剪必须三处对齐（呈现/查找/执行）。
 * 红线 C3：配置漂移不得打挂 harness — 启动期名单校验，未知名 warn+跳过。
 * 红线 C4：保底工具数 + 三道兜底。
 *
 * 实测：32,829 → 6,821 字符（−79.2%），§3.1/§3.3/§3.4。
 */
import { telemetry } from './telemetry.js'

/**
 * 意图域定义：每个意图对应一组工具白名单。
 * 工具名必须是 DSH 注册表里真实存在的全局工具名（C3 强校验）。
 */
const INTENT_DOMAINS = {
  'read-only': {
    description: '只读探查：读文件、搜索、grep',
    tools: ['read', 'grep', 'glob'],
  },
  'code-edit': {
    description: '代码编辑：读写+搜索',
    tools: ['read', 'edit', 'grep', 'glob', 'bash'],
  },
  'full': {
    description: '全工具集（不裁）',
    tools: null,  // null = 不 restrict
  },
}

export class CognitiveCompressor {
  #domains = {}       // 意图 → 工具白名单
  #minTools = 6       // C4 保底工具数
  #onUnknown = 'warn' // C3 配置漂移处理

  configure(domains, minTools, onUnknownTool) {
    this.#domains = domains && Object.keys(domains).length ? domains : INTENT_DOMAINS
    this.#minTools = minTools ?? 6
    this.#onUnknown = onUnknownTool || 'warn'
  }

  /** 简单意图分类：基于最近一条 user message 关键词 */
  classifyIntent(messages) {
    const lastUser = [...(messages || [])].reverse().find(m => m.role === 'user')
    if (!lastUser) return 'full'
    const text = (typeof lastUser.content === 'string' ? lastUser.content : '').toLowerCase()

    // 包含编辑/写入关键词 → code-edit
    if (/编辑|修改|写入|create|edit|write|fix|refactor|implement/.test(text)) return 'code-edit'
    // 包含读/查/看关键词 → read-only
    if (/读|查|看|搜索|找|read|search|find|grep|look|show|list/.test(text)) return 'read-only'
    // 默认全工具
    return 'full'
  }

  /** 获取指定意图的工具白名单 */
  getToolAllowlist(intent) {
    const domain = this.#domains[intent]
    if (!domain) return null
    return domain.tools  // null = 不裁
  }

  /**
   * 在 agent/session-start 中调用：
   * agent.ctx.tools.restrict({allow: [...]})
   *
   * C3：启动期校验名单 — 用 ctx.tools.schemas() 获取已注册工具名，
   *     未知名默认 warn + 跳过。
   * C4：保底工具数 — 裁剪后低于 minTools 则不裁。
   */
  applyRestrict(agentCtx, registeredToolNames, intent) {
    const allow = this.getToolAllowlist(intent)
    if (!allow) {
      telemetry.emit('compress/skip', { intent, reason: 'full-domain' })
      return { restricted: false, intent }
    }

    // C3：校验名单 — 过滤掉未注册的工具名
    const registered = new Set(registeredToolNames)
    const valid = []
    const unknown = []
    for (const name of allow) {
      if (registered.has(name)) {
        valid.push(name)
      } else {
        unknown.push(name)
      }
    }
    if (unknown.length) {
      telemetry.emit('compress/unknown-tools', { unknown, handling: this.#onUnknown })
      if (this.#onUnknown === 'throw') {
        throw new Error(`LAO compress: unknown tools in allow list: ${unknown.join(', ')}`)
      }
      // 'warn' → 跳过未知名，继续
    }

    // C4：保底工具数
    if (valid.length < this.#minTools) {
      telemetry.emit('compress/fallback', { intent, validCount: valid.length, minTools: this.#minTools, reason: 'below-minimum' })
      return { restricted: false, intent, reason: 'below-minimum' }
    }

    try {
      agentCtx.tools.restrict({ allow: valid })
      telemetry.emit('compress/restrict', { intent, allow: valid, count: valid.length })
      return { restricted: true, intent, allow: valid, count: valid.length }
    } catch (err) {
      telemetry.emit('compress/error', { intent, error: err.message })
      // C4 兜底 1：restrict 失败 → 不裁，保持原生
      return { restricted: false, intent, reason: 'restrict-failed', error: err.message }
    }
  }

  get minTools() { return this.#minTools }
}

export const compressor = new CognitiveCompressor()
