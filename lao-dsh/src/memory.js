/**
 * memory.js — L2 经验/记忆注入
 *
 * 主机制：ctx.systemPrompt.section(id, content, scope) 注入有序段。
 * 作用域可遮蔽：高优先级 scope 覆盖低优先级同名 section。
 *
 * §5.2：经验/记忆注入 — 从本地经验文件或 RAL 拉取相关记忆，
 * 在 agent/session-start 或 system-prompt/assemble 时注入 system prompt。
 *
 * 红线 C1：不得改写 messages 结构。本模块只操作 system prompt section，
 * 不触碰 messages 数组。
 */
import { readFileSync } from 'node:fs'
import { telemetry } from './telemetry.js'

export class MemoryInjector {
  #sections = new Map()  // sectionId → {content, scope, priority}
  #enabled = false

  configure(enabled) {
    this.#enabled = enabled || false
  }

  get enabled() { return this.#enabled }

  /**
   * 从本地 JSON 文件加载经验记忆。
   * 格式：[{id, content, scope?, priority?}]
   */
  loadFromFile(filePath) {
    if (!filePath) return
    try {
      const raw = readFileSync(filePath, 'utf-8')
      const entries = JSON.parse(raw)
      if (!Array.isArray(entries)) return
      for (const entry of entries) {
        if (entry.id && entry.content) {
          this.#sections.set(entry.id, {
            content: entry.content,
            scope: entry.scope || 'session',
            priority: entry.priority || 0,
          })
        }
      }
      telemetry.emit('memory/loaded', { count: entries.length, source: filePath })
    } catch (err) {
      telemetry.emit('memory/load-error', { source: filePath, error: err.message })
    }
  }

  /** 手动注入一条记忆 */
  set(id, content, scope, priority) {
    this.#sections.set(id, { content, scope: scope || 'session', priority: priority || 0 })
  }

  /** 移除一条记忆 */
  remove(id) {
    this.#sections.delete(id)
  }

  /**
   * 在 system-prompt/assemble 中调用：
   * 遍历已加载的 section，逐个调用 ctx.systemPrompt.section()。
   *
   * @param {object} systemPromptCtx — ctx.systemPrompt 对象
   */
  injectAll(systemPromptCtx) {
    if (!this.#enabled || !systemPromptCtx) return

    // 按 priority 降序注入（高优先级先注入，后注入的同名段可遮蔽）
    const sorted = [...this.#sections.entries()]
      .sort((a, b) => (a[1].priority || 0) - (b[1].priority || 0))

    let injected = 0
    for (const [id, { content, scope }] of sorted) {
      try {
        if (typeof systemPromptCtx.section === 'function') {
          systemPromptCtx.section(id, content, scope)
          injected++
        }
      } catch (err) {
        telemetry.emit('memory/inject-error', { id, error: err.message })
      }
    }
    telemetry.emit('memory/injected', { count: injected, total: this.#sections.size })
  }

  get size() { return this.#sections.size }
  get entries() { return [...this.#sections.entries()].map(([id, s]) => ({ id, ...s })) }
}

export const memory = new MemoryInjector()
