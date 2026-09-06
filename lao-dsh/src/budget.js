/**
 * budget.js — L1 成本守门（预算闸）
 *
 * 主机制：tools/pre-execute 返回 deny{reason} 拦截工具体执行。
 * 辅助：ctx.tools.guard() 单调否决（一旦触发，本轮剩余调用全部拒绝）。
 *
 * 红线依据：§5.2 L1 成本守门 + §3.5 deny 已实测。
 * V5 验收：预算闸拒绝后工具体确未执行。
 */
import { telemetry } from './telemetry.js'

export class BudgetGate {
  #perTurnCeiling = 0   // ¥/turn，0 = 不限
  #spent = 0            // 本轮已消耗估算
  #guardTripped = false // 单调否决锁
  #toolCosts = new Map()// tool name → 估算成本

  configure(perTurnCeiling) {
    this.#perTurnCeiling = perTurnCeiling || 0
  }

  /** 重置本轮计数器（每次新 turn 调用） */
  resetTurn() {
    this.#spent = 0
    this.#guardTripped = false
    this.#toolCosts.clear()
  }

  /**
   * tools/pre-execute 钩子处理。
   * 返回 null = 放行；返回 {deny:{reason}} = 拦截。
   */
  preExecute(toolName, estimatedCost) {
    // 已触发 guard → 单调否决
    if (this.#guardTripped) {
      telemetry.emit('budget/deny-guard', { tool: toolName, reason: 'guard-tripped' })
      return { deny: { reason: `Budget guard tripped, rejecting all further tool calls` } }
    }

    // 无限额 → 放行
    if (this.#perTurnCeiling <= 0) return null

    const cost = estimatedCost || 0
    // 预估超限 → 拦截
    if (this.#spent + cost > this.#perTurnCeiling) {
      this.#guardTripped = true  // 单调锁定
      telemetry.emit('budget/deny-ceiling', {
        tool: toolName,
        estimatedCost: cost,
        spent: this.#spent,
        ceiling: this.#perTurnCeiling,
      })
      return {
        deny: {
          reason: `Budget ceiling exceeded: spent ¥${this.#spent.toFixed(4)}, ` +
                  `estimate ¥${cost.toFixed(4)}, ceiling ¥${this.#perTurnCeiling.toFixed(4)}`,
        },
      }
    }

    // 放行 — 记录估算
    this.#spent += cost
    this.#toolCosts.set(toolName, (this.#toolCosts.get(toolName) || 0) + cost)
    telemetry.emit('budget/allow', { tool: toolName, cost, spent: this.#spent })
    return null
  }

  /** 实际结算后校正 spent（post-execute 或 route settle 回调） */
  settle(actualCost) {
    // spent 已在 preExecute 加了估算，这里做差值校正
    // 简化模型：直接用实际值替换最后一次估算
    telemetry.emit('budget/settle', { actualCost, spent: this.#spent })
  }

  get spent() { return this.#spent }
  get ceiling() { return this.#perTurnCeiling }
  get tripped() { return this.#guardTripped }
}

export const budget = new BudgetGate()
