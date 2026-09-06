/**
 * cache.js — L1 请求指纹缓存
 *
 * 按 GenerateOptions 指纹（messages hash + model + tools hash）匹配，
 * 命中时回放 StreamChunk 序列，不出网、零成本。
 *
 * 协议义务（V10）：回放序列必须通过 7 条协议校验，尤其 usage 先于 finish。
 * 缓存键 bug 曾直接造成成本事故（183号 §1.3），故缓存模块独立于路由。
 *
 * 状态：挂点已证（adapter 层可完全接管 stream），缓存命中逻辑属签章后开发。
 */
import { createHash } from 'node:crypto'
import { telemetry } from './telemetry.js'

export class RequestCache {
  #store = new Map()  // fingerprint → { chunks, createdAt, hits }
  #ttlMs = 0
  #maxEntries = 500

  configure(ttlMs, maxEntries) {
    this.#ttlMs = ttlMs || 0
    this.#maxEntries = maxEntries || 500
  }

  /** 计算请求指纹 */
  fingerprint(options) {
    const h = createHash('sha256')
    h.update(JSON.stringify(options.messages || []))
    h.update(options.model || '')
    h.update(JSON.stringify((options.tools || []).map(t => t.name).sort()))
    h.update(String(options.maxTokens || ''))
    h.update(String(options.temperature || ''))
    return h.digest('hex').slice(0, 24)
  }

  /** 查找缓存 */
  lookup(fp) {
    const entry = this.#store.get(fp)
    if (!entry) return null
    // TTL 检查
    if (this.#ttlMs > 0 && Date.now() - entry.createdAt > this.#ttlMs) {
      this.#store.delete(fp)
      return null
    }
    entry.hits++
    telemetry.emit('cache/hit', { fingerprint: fp, hits: entry.hits })
    return entry.chunks
  }

  /** 存入缓存 */
  store(fp, chunks) {
    // LRU 淘汰
    if (this.#store.size >= this.#maxEntries) {
      const oldest = this.#store.keys().next().value
      this.#store.delete(oldest)
    }
    this.#store.set(fp, { chunks, createdAt: Date.now(), hits: 0 })
    telemetry.emit('cache/store', { fingerprint: fp, storeSize: this.#store.size })
  }

  /** 回放缓存序列为 AsyncIterable<StreamChunk> */
  async *replay(chunks) {
    for (const chunk of chunks) {
      yield chunk
    }
  }

  get size() { return this.#store.size }
  get stats() {
    let totalHits = 0
    for (const e of this.#store.values()) totalHits += e.hits
    return { entries: this.#store.size, totalHits }
  }
}

export const cache = new RequestCache()
