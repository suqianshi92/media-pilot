import { describe, expect, it } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'

/**
 * 静态断言: agent-panel.tsx 的 FreeformInput 内部 <textarea> 不得再有
 * placeholder 属性, 帮助文本 <p>freeformHint 必须保留.
 *
 * 之所以用静态读文件而不是 mount 组件:
 * - FreeformInput 是 agent-panel.tsx 的私有函数 (不导出), mount 起来
 *   还要 mock SSE / useMutation, 性价比低.
 * - 这个变更的关键约束就是 "placeholder 这个 prop 真的从源码里删掉了",
 *   静态读文件能直接证明.
 */

describe('FreeformInput — placeholder 已移除', () => {
  const source = readFileSync(
    resolve(__dirname, '../agent-panel.tsx'),
    'utf8'
  )

  // 找到 FreeformInput 函数体 — 从 "function FreeformInput(" 起,
  // 先跨过参数列表, 再用嵌套花括号配对的方式抓到函数末尾.
  const start = source.indexOf('function FreeformInput(')
  if (start < 0) {
    throw new Error('无法在 agent-panel.tsx 中定位 FreeformInput')
  }
  // 跳过参数列表 (第一个 `{` 之后到第一个 `)` 结束, 再到函数体 `{`)
  const paramStart = source.indexOf('{', start)
  const paramEnd = source.indexOf(')', paramStart)
  if (paramStart < 0 || paramEnd < 0) {
    throw new Error('无法定位 FreeformInput 参数列表')
  }
  // 函数体从 paramEnd 之后第一个 `{` 开始
  const bodyStart = source.indexOf('{', paramEnd)
  if (bodyStart < 0) {
    throw new Error('无法定位 FreeformInput 函数体起始花括号')
  }
  let depth = 0
  let end = -1
  for (let i = bodyStart; i < source.length; i++) {
    if (source[i] === '{') {
      depth += 1
    } else if (source[i] === '}') {
      depth -= 1
      if (depth === 0) {
        end = i + 1
        break
      }
    }
  }
  if (end < 0) {
    throw new Error('无法在 agent-panel.tsx 中定位 FreeformInput 结束花括号')
  }
  const freeformBody = source.slice(start, end)

  it('<textarea> 不再设置 placeholder 属性', () => {
    // textarea 在 FreeformInput 体内, 但不应有 placeholder=
    expect(freeformBody).not.toMatch(/<textarea[\s\S]*?placeholder=/)
  })

  it('className 不再含 placeholder:text-muted-foreground (Tailwind placeholder 样式)', () => {
    expect(freeformBody).not.toMatch(/placeholder:text-muted-foreground/)
  })

  it('下方帮助文本 <p>freeformHint 仍存在', () => {
    expect(freeformBody).toMatch(/agent\.freeformHint/)
  })

  it('textarea 仍保留 rows / value / onChange / onKeyDown / disabled', () => {
    expect(freeformBody).toMatch(/<textarea[\s\S]*?rows=\{2\}/)
    expect(freeformBody).toMatch(/<textarea[\s\S]*?value=\{inputText\}/)
    expect(freeformBody).toMatch(/<textarea[\s\S]*?onChange=/)
    expect(freeformBody).toMatch(/<textarea[\s\S]*?onKeyDown=\{handleKeyDown\}/)
    expect(freeformBody).toMatch(/<textarea[\s\S]*?disabled=/)
  })
})

describe('i18n key 仍保留 (本期不删)', () => {
  it('zh.json 仍含 agent.freeformPlaceholder', async () => {
    const { readFile } = await import('fs/promises')
    const zh = await readFile(
      resolve(__dirname, '../../../i18n/locales/zh.json'),
      'utf8'
    )
    expect(zh).toMatch(/"freeformPlaceholder"\s*:/)
  })

  it('en.json 仍含 agent.freeformPlaceholder', async () => {
    const { readFile } = await import('fs/promises')
    const en = await readFile(
      resolve(__dirname, '../../../i18n/locales/en.json'),
      'utf8'
    )
    expect(en).toMatch(/"freeformPlaceholder"\s*:/)
  })
})
