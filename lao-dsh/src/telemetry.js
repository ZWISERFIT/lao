/**
 * telemetry.js — JSONL 埋点（复用探针格式）
 *
 * 每次拦截事件写一行 JSONL，供事后审计与 V8–V14 验收取证。
 * 落盘路径由 Config.telemetry.jsonlPath 指定；为空则不写。
 */
import { appendFileSync, mkdirSync } from 'node:fs'
import { dirname } from 'node:path'

let seq = 0

export class Telemetry {
  #path = ''
  #tag = 'lao-dsh'

  configure(jsonlPath, tag) {
    this.#path = jsonlPath || ''
    this.#tag = tag || 'lao-dsh'
  }

  get enabled() { return Boolean(this.#path) }

  emit(event, data) {
    if (!this.#path) return
    const line = { seq: ++seq, t: Date.now(), tag: this.#tag, event, ...data }
    try {
      mkdirSync(dirname(this.#path), { recursive: true })
      appendFileSync(this.#path, JSON.stringify(line) + '\n')
    } catch {
      // 埋盘失败不得阻塞主流程
    }
  }
}

export const telemetry = new Telemetry()
