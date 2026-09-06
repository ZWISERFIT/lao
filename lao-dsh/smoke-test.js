#!/usr/bin/env node
/**
 * smoke-test.js — lao-dsh 装载冒烟测试
 *
 * 验证：
 *   1. 全部模块可 import
 *   2. Config schema 结构正确
 *   3. apply() 在 enabled=false 时零副作用
 *   4. 各模块 configure() 不抛错
 *   5. BudgetGate deny 逻辑正确
 *   6. RequestCache fingerprint + store + lookup 闭环
 *   7. CognitiveCompressor classifyIntent 分类正确
 */

// 模拟 peer deps（服务器上没有真实 DSH 包，用 stub）
import { createRequire } from 'node:module'
import { writeFileSync, mkdirSync } from 'node:fs'

// Stub @deepseek-ai/dsh-llm
const stubDir = '/tmp/lao-dsh-stubs'
mkdirSync(stubDir, { recursive: true })

// 写 stub 模块
writeFileSync(`${stubDir}/dsh-llm.js`, `
export class LlmAdapter {}
export class LlmError extends Error {
  constructor(msg, code) { super(msg); this.code = code }
}
`)
writeFileSync(`${stubDir}/package.json`, '{"name":"stub","type":"module","exports":{".":"./index.js"}}')

// 用 import() 动态加载，先设 NODE_PATH
// 实际上我们用相对路径 import，所以直接测试各模块

let pass = 0, fail = 0

function assert(cond, label) {
  if (cond) { console.log(`  ✓ ${label}`); pass++ }
  else { console.log(`  ✗ ${label}`); fail++ }
}

async function main() {
  console.log('=== lao-dsh Smoke Test ===\n')

  // 1. Import 全部模块
  console.log('[1] Module imports')
  const tel = await import('./src/telemetry.js')
  assert(tel.Telemetry && tel.telemetry, 'telemetry.js exports Telemetry + singleton')

  const rtr = await import('./src/router.js')
  assert(rtr.LaoRouterAdapter, 'router.js exports LaoRouterAdapter')

  const cch = await import('./src/cache.js')
  assert(cch.RequestCache && cch.cache, 'cache.js exports RequestCache + singleton')

  const cmp = await import('./src/compress.js')
  assert(cmp.CognitiveCompressor && cmp.compressor, 'compress.js exports CognitiveCompressor + singleton')

  const bgt = await import('./src/budget.js')
  assert(bgt.BudgetGate && bgt.budget, 'budget.js exports BudgetGate + singleton')

  const hrv = await import('./src/harvest.js')
  assert(hrv.ExperienceHarvester && bgt.budget, 'harvest.js exports ExperienceHarvester + singleton')

  const mem = await import('./src/memory.js')
  assert(mem.MemoryInjector && mem.memory, 'memory.js exports MemoryInjector + singleton')

  const idx = await import('./src/index.js')
  assert(idx.name === 'lao-dsh', 'index.js name = lao-dsh')
  assert(typeof idx.apply === 'function', 'index.js exports apply()')
  assert(idx.Config?.type === 'object', 'index.js exports Config schema')
  assert(Array.isArray(idx.inject), 'index.js exports inject array')

  // 2. Config 结构
  console.log('\n[2] Config schema')
  const props = idx.Config.properties
  assert(props.enabled?.default === false, 'enabled default = false (C5)')
  assert(props.route?.properties?.downstream?.type === 'array', 'route.downstream is array')
  assert(props.compress?.properties?.minTools?.default === 6, 'compress.minTools default = 6 (C4)')
  assert(props.budget?.properties?.perTurnCeiling?.default === 0, 'budget.perTurnCeiling default = 0')

  // 3. apply() enabled=false → 零副作用
  console.log('\n[3] apply() disabled mode')
  const mockCtx = { effect: () => {}, llm: { registerAdapter: () => { throw new Error('should not be called') } } }
  try {
    idx.apply(mockCtx, { enabled: false })
    assert(true, 'apply(enabled=false) returns without error')
  } catch (e) {
    assert(false, `apply(enabled=false) threw: ${e.message}`)
  }

  // 4. 各模块 configure() 不抛错
  console.log('\n[4] Module configure()')
  try {
    tel.telemetry.configure('/tmp/lao-test.jsonl')
    assert(true, 'telemetry.configure() OK')
  } catch (e) { assert(false, `telemetry.configure: ${e.message}`) }

  try {
    cch.cache.configure(5000, 100)
    assert(true, 'cache.configure() OK')
  } catch (e) { assert(false, `cache.configure: ${e.message}`) }

  try {
    cmp.compressor.configure({}, 6, 'warn')
    assert(true, 'compress.configure() OK')
  } catch (e) { assert(false, `compress.configure: ${e.message}`) }

  try {
    bgt.budget.configure(0.1)
    assert(true, 'budget.configure() OK')
  } catch (e) { assert(false, `budget.configure: ${e.message}`) }

  try {
    hrv.harvester.configure(true, 'http://localhost:9999/ral')
    assert(true, 'harvest.configure() OK')
  } catch (e) { assert(false, `harvest.configure: ${e.message}`) }

  try {
    mem.memory.configure(true)
    assert(true, 'memory.configure() OK')
  } catch (e) { assert(false, `memory.configure: ${e.message}`) }

  // 5. BudgetGate deny 逻辑
  console.log('\n[5] BudgetGate logic')
  const bg = new bgt.BudgetGate()
  bg.configure(0.05)
  bg.resetTurn()
  const allow = bg.preExecute('read', 0.01)
  assert(allow === null, 'under ceiling → allow (null)')
  const deny = bg.preExecute('bash', 0.1)
  assert(deny?.deny?.reason, 'over ceiling → deny with reason')
  assert(bg.tripped === true, 'guard tripped after deny')
  const afterGuard = bg.preExecute('grep', 0.001)
  assert(afterGuard?.deny?.reason, 'after trip → all denied (monotonic)')

  // 6. RequestCache 闭环
  console.log('\n[6] RequestCache cycle')
  const rc = new cch.RequestCache()
  rc.configure(60000, 10)
  const fp = rc.fingerprint({ messages: [{ role: 'user', content: 'hi' }], model: 'test' })
  assert(typeof fp === 'string' && fp.length === 24, `fingerprint = ${fp}`)
  rc.store(fp, [{ type: 'text-delta', text: 'hello' }])
  const hit = rc.lookup(fp)
  assert(hit && hit[0].text === 'hello', 'store → lookup returns cached chunks')
  assert(rc.size === 1, 'cache size = 1')

  // 7. CognitiveCompressor classifyIntent
  console.log('\n[7] CognitiveCompressor intent classification')
  const cc = new cmp.CognitiveCompressor()
  cc.configure({}, 6, 'warn')
  assert(cc.classifyIntent([{ role: 'user', content: '请读一下这个文件' }]) === 'read-only', '读 → read-only')
  assert(cc.classifyIntent([{ role: 'user', content: 'edit this function' }]) === 'code-edit', 'edit → code-edit')
  assert(cc.classifyIntent([{ role: 'user', content: 'do everything' }]) === 'full', 'generic → full')

  // 结果
  console.log(`\n=== Result: ${pass} passed, ${fail} failed ===`)
  process.exit(fail > 0 ? 1 : 0)
}

main().catch(e => { console.error('FATAL:', e); process.exit(1) })
