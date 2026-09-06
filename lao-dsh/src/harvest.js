/**
 * harvest.js — L2 经验萃取（tools/result → RAL）
 *
 * 主机制：tools/result 只读捕获冻结结果 → POST 到 RAL 端点。
 * 辅助：tools/post-execute 仅在需改写结果时使用（本插件不改写，仅捕获）。
 *
 * §5.2：经验萃取 — tools/result 拿到冻结结果后，提取决策锚点，
 * 写入 RAL（Remote Anchor Ledger）端点，供跨生态经验沉淀。
 *
 * 萃取格式：{anchor_type, tool, input_summary, output_summary, outcome, ts}
 * outcome = 'success' | 'failure' | 'partial'
 */
import { telemetry } from './telemetry.js'

export class ExperienceHarvester {
  #ralEndpoint = ''
  #enabled = false

  configure(enabled, ralEndpoint) {
    this.#enabled = enabled || false
    this.#ralEndpoint = ralEndpoint || ''
  }

  get enabled() { return this.#enabled && Boolean(this.#ralEndpoint) }

  /**
   * tools/result 钩子处理。
   * result 是冻结的只读对象，包含 tool name、args、返回值、耗时等。
   * 本方法只读，不修改 result。
   */
  onToolResult(result) {
    if (!this.enabled) return
    if (!result) return

    const anchor = this.#extractAnchor(result)
    if (!anchor) return

    telemetry.emit('harvest/extract', {
      anchorType: anchor.anchor_type,
      tool: anchor.tool,
      outcome: anchor.outcome,
    })

    // 异步发送到 RAL，不阻塞主流程
    this.#submitToRal(anchor).catch(err => {
      telemetry.emit('harvest/ral-error', { error: err.message })
    })
  }

  /** 从冻结结果中提取决策锚点 */
  #extractAnchor(result) {
    const toolName = result.tool || result.name || 'unknown'
    const ok = result.ok !== false && !result.error
    const outcome = ok ? 'success' : 'failure'

    return {
      anchor_type: 'tool_result',
      tool: toolName,
      input_summary: this.#summarize(result.input || result.args),
      output_summary: this.#summarize(result.output || result.value),
      outcome,
      error: result.error || null,
      duration_ms: result.durationMs || result.duration || 0,
      ts: Date.now(),
    }
  }

  /** 截断摘要，避免 RAL 载荷过大 */
  #summarize(data, maxLen = 500) {
    if (!data) return null
    const str = typeof data === 'string' ? data : JSON.stringify(data)
    if (!str) return null
    return str.length > maxLen ? str.slice(0, maxLen) + '…' : str
  }

  /** POST 锚点到 RAL 端点 */
  async #submitToRal(anchor) {
    if (!this.#ralEndpoint) return
    const res = await fetch(this.#ralEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(anchor),
    })
    if (!res.ok) {
      throw new Error(`RAL ${res.status}: ${(await res.text().catch(() => '')).slice(0, 200)}`)
    }
    telemetry.emit('harvest/submitted', { tool: anchor.tool, outcome: anchor.outcome })
  }
}

export const harvester = new ExperienceHarvester()
