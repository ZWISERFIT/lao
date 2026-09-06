/**
 * router.js — L1 智能路由 adapter
 *
 * LaoRouterAdapter extends LlmAdapter，注册为 'lao' provider。
 * stream() 内按成本从白名单（route.downstream）选最优下游 provider，
 * 通过 HTTP 直连下游 API（OpenAI-compatible /chat/completions）。
 *
 * 红线 C7：provider 锁定 = 白名单制 + 用户可覆盖。
 *   - 默认：route.downstream 候选集内按成本选最优
 *   - 用户指定 provider 后：仅在该 provider 的模型列表内匹配，不得越出
 *
 * 协议义务（§5.3）：
 *   1. usage 在 finish 之前发出
 *   2. tool-call arguments 端到端原始 JSON 字符串
 *   3. block index 按首见顺序分配
 *   4. 错误只有 LlmError 或 finish{kind:'error'|'aborted'}
 *   5. 遵守 options.signal
 *   6. 不支持的选项抛 LlmError('UNSUPPORTED_OPTION')
 */
import { LlmAdapter, LlmError } from '@deepseek-ai/dsh-llm'
import { telemetry } from './telemetry.js'

/**
 * 下游 provider 配置表（从 Config.route.downstream 解析）。
 * 每项至少需要 { name, baseUrl, apiKey, models: [{id, inputPerMToken, outputPerMToken}] }。
 * 实际部署时从 LAO 已有的 PROVIDER_CONFIG 读取。
 */
const DEFAULT_DOWNSTREAM = []

export class LaoRouterAdapter extends LlmAdapter {
  #downstream = []         // [{name, baseUrl, apiKey, models}]
  #costTable = new Map()   // model → {input, output} ¥/M tokens
  #userOverride = null     // 用户指定的 provider（C7）

  constructor(downstream = []) {
    super()
    this.#downstream = downstream.length ? downstream : DEFAULT_DOWNSTREAM
    this.#rebuildCostTable()
  }

  #rebuildCostTable() {
    this.#costTable.clear()
    for (const p of this.#downstream) {
      for (const m of (p.models || [])) {
        this.#costTable.set(m.id, {
          provider: p.name,
          input: m.inputPerMToken || 0,
          output: m.outputPerMToken || 0,
        })
      }
    }
  }

  /** C7：用户覆盖 — 锁定到指定 provider */
  setUserOverride(providerName) {
    this.#userOverride = providerName || null
    telemetry.emit('router/user-override', { provider: providerName })
  }

  /** 从候选中选成本最优的 provider+model */
  #selectBest(modelHint) {
    // 如果用户指定了 provider，只在该 provider 内选
    if (this.#userOverride) {
      const p = this.#downstream.find(d => d.name === this.#userOverride)
      if (!p) throw new LlmError(`User-override provider "${this.#userOverride}" not in whitelist`, 'UNSUPPORTED_OPTION')
      const model = this.#findModelInProvider(p, modelHint)
      return { provider: p, model }
    }
    // 白名单内按成本选最优
    let best = null
    let bestCost = Infinity
    for (const p of this.#downstream) {
      const model = this.#findModelInProvider(p, modelHint)
      if (!model) continue
      // 估算成本（用 output 价格排序，input 价格作 tiebreaker）
      const cost = model.outputPerMToken * 1000 + model.inputPerMToken
      if (cost < bestCost) {
        bestCost = cost
        best = { provider: p, model }
      }
    }
    if (!best) throw new LlmError('No downstream provider available in whitelist', 'UNSUPPORTED_OPTION')
    return best
  }

  #findModelInProvider(p, modelHint) {
    if (!p.models?.length) return null
    // 精确匹配
    if (modelHint) {
      const exact = p.models.find(m => m.id === modelHint)
      if (exact) return exact
    }
    // 返回第一个（默认模型）
    return p.models[0]
  }

  // ── LlmAdapter 接口实现 ──

  providerInfo(provider) {
    return {
      id: provider,
      name: 'LAO Router',
      capabilities: { streaming: true, toolCalls: true },
    }
  }

  async listModels(_provider) {
    // 聚合全部下游模型
    const models = []
    for (const p of this.#downstream) {
      for (const m of (p.models || [])) {
        models.push({ id: m.id, provider: p.name })
      }
    }
    return models
  }

  async resolveModel(provider, model, _signal) {
    const info = this.#costTable.get(model)
    return {
      provider,
      model,
      contextWindow: 128000,
      maxOutputTokens: 8192,
      pricing: info ? { inputPerMToken: info.input, outputPerMToken: info.output } : undefined,
    }
  }

  async prepareCall(provider, model, signal) {
    const info = await this.resolveModel(provider, model, signal)
    return {
      modelInfo: info,
      stream: (options) => this.stream(options),
    }
  }

  /**
   * 核心：代理型 stream()。
   * 选下游 → HTTP POST /chat/completions → 转译为 StreamChunk 序列。
   */
  async *stream(options) {
    const modelHint = options.model
    const { provider: downstream, model: selectedModel } = this.#selectBest(modelHint)

    telemetry.emit('llm/stream-out', {
      originalModel: modelHint,
      routedProvider: downstream.name,
      routedModel: selectedModel.id,
      userOverride: this.#userOverride,
    })

    const baseUrl = downstream.baseUrl.replace(/\/$/, '')
    const url = `${baseUrl}/chat/completions`

    // 构造下游请求
    const body = {
      model: selectedModel.id,
      messages: options.messages || [],
      stream: true,
      max_tokens: options.maxTokens,
      temperature: options.temperature,
    }
    // 工具调用透传
    if (options.tools?.length) {
      body.tools = options.tools.map(t => ({
        type: 'function',
        function: { name: t.name, description: t.description, parameters: t.parameters },
      }))
    }

    let response
    try {
      response = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${downstream.apiKey}`,
        },
        body: JSON.stringify(body),
        signal: options.signal,
      })
    } catch (err) {
      if (options.signal?.aborted) {
        yield { type: 'finish', kind: 'aborted' }
        return
      }
      throw new LlmError(`Downstream request failed: ${err.message}`, 'PROVIDER_ERROR')
    }

    if (!response.ok) {
      const text = await response.text().catch(() => '')
      throw new LlmError(`Downstream ${response.status}: ${text.slice(0, 200)}`, 'PROVIDER_ERROR')
    }

    // 转译 SSE → StreamChunk
    yield* this.#translateSSE(response, options.signal)
  }

  /** 将 OpenAI-compatible SSE 流转译为 DSH StreamChunk 序列 */
  async *#translateSSE(response, signal) {
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let usageReported = false
    const blockIndexMap = new Map()  // tool call id → index
    let nextBlockIndex = 0

    try {
      while (true) {
        if (signal?.aborted) {
          yield { type: 'finish', kind: 'aborted' }
          return
        }
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const data = line.slice(6).trim()
          if (data === '[DONE]') continue
          try {
            const chunk = JSON.parse(data)
            // usage（必须在 finish 之前 — 协议义务 #1）
            if (chunk.usage && !usageReported) {
              yield {
                type: 'usage',
                inputTokens: chunk.usage.prompt_tokens || 0,
                outputTokens: chunk.usage.completion_tokens || 0,
              }
              usageReported = true
            }
            // delta
            const delta = chunk.choices?.[0]?.delta
            if (!delta) continue
            // 文本内容
            if (delta.content) {
              yield { type: 'text-delta', text: delta.content }
            }
            // 工具调用（协议义务 #2: arguments 端到端原始 JSON 字符串）
            if (delta.tool_calls) {
              for (const tc of delta.tool_calls) {
                let idx = blockIndexMap.get(tc.id)
                if (idx === undefined && tc.id) {
                  // 新 block — 按首见顺序分配 index（协议义务 #3）
                  idx = nextBlockIndex++
                  blockIndexMap.set(tc.id, idx)
                  yield {
                    type: 'tool-call-start',
                    index: idx,
                    id: tc.id,
                    name: tc.function?.name || '',
                  }
                }
                if (idx !== undefined && tc.function?.arguments) {
                  // argumentsDelta: 原始 JSON 字符串分片
                  yield {
                    type: 'tool-call-arguments-delta',
                    index: idx,
                    argumentsDelta: tc.function.arguments,
                  }
                }
              }
            }
            // finish_reason
            const reason = chunk.choices?.[0]?.finish_reason
            if (reason) {
              // 补发 usage（如果上游没发 — 防御性兜底）
              if (!usageReported) {
                yield { type: 'usage', inputTokens: 0, outputTokens: 0 }
              }
              yield {
                type: 'finish',
                kind: reason === 'stop' ? 'stop' : reason === 'tool_calls' ? 'tool-calls' : 'stop',
              }
              return
            }
          } catch {
            // 跳过无法解析的行
          }
        }
      }
    } finally {
      reader.releaseLock()
    }

    // 流结束但没收到 finish — 防御性兜底
    if (!usageReported) {
      yield { type: 'usage', inputTokens: 0, outputTokens: 0 }
    }
    yield { type: 'finish', kind: 'stop' }
  }
}
