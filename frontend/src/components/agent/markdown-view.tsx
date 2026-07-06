/**
 * MarkdownView — 无 dangerouslySetInnerHTML 的轻量 Markdown 渲染器.
 *
 * 设计约束:
 * - 所有文本通过 React 插值渲染 (自动 HTML 转义); <script> 等任意 HTML
 *   必须原样显示为字符, 不得执行.
 * - 不引入第三方依赖.
 * - 支持: 段落 / 列表 (有序无序) / 表格 / 标题 (#..####) / 行内 code /
 *   code block (```) / 粗体 (**) / 斜体 (*) / 链接 ([]()).
 * - 解析失败时回退到 <p>{text}</p>.
 *
 * 故意保持单文件, 单组件 — 解析逻辑在 parseMarkdown 内联展开, 不抽象
 * 子解析器, 以便阅读与测试.
 */

import { Fragment, type ReactNode } from 'react'

interface MarkdownViewProps {
  content: string
}

interface Block {
  kind: 'paragraph' | 'heading' | 'ul' | 'ol' | 'code' | 'table'
  level?: number
  lines: string[]
  /** table: 首行为表头, 第二行为分隔, 后续为数据行 */
  rows?: string[][]
}

function parseMarkdown(content: string): Block[] {
  const lines = content.replace(/\r\n/g, '\n').split('\n')
  const blocks: Block[] = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    // 跳过空行
    if (line.trim() === '') {
      i += 1
      continue
    }
    // code block: ```...```
    if (line.startsWith('```')) {
      const codeLines: string[] = []
      i += 1
      while (i < lines.length && !lines[i].startsWith('```')) {
        codeLines.push(lines[i])
        i += 1
      }
      i += 1 // 跳过结束的 ```
      blocks.push({ kind: 'code', lines: codeLines })
      continue
    }
    // heading: # .. ####
    const headingMatch = line.match(/^(#{1,4})\s+(.*)$/)
    if (headingMatch) {
      blocks.push({
        kind: 'heading',
        level: headingMatch[1].length,
        lines: [headingMatch[2]],
      })
      i += 1
      continue
    }
    // unordered list
    if (/^[-*]\s+/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^[-*]\s+/, ''))
        i += 1
      }
      blocks.push({ kind: 'ul', lines: items })
      continue
    }
    // ordered list
    if (/^\d+\.\s+/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\d+\.\s+/.test(lines[i])) {
        const itemLines = [lines[i].replace(/^\d+\.\s+/, '')]
        i += 1
        while (i < lines.length) {
          if (/^\d+\.\s+/.test(lines[i])) break
          if (lines[i].trim() === '') {
            const nextLine = lines[i + 1] ?? ''
            if (/^\d+\.\s+/.test(nextLine) || nextLine.trim() === '') {
              i += 1
              continue
            }
            break
          }
          if (/^\s+/.test(lines[i])) {
            itemLines.push(lines[i].trim())
            i += 1
            continue
          }
          break
        }
        items.push(itemLines.join('\n'))
      }
      blocks.push({ kind: 'ol', lines: items })
      continue
    }
    // table: 首行 |..|, 第二行 |----|...
    if (line.trim().startsWith('|') && line.trim().endsWith('|')) {
      if (
        i + 1 < lines.length
        && /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1])
      ) {
        const splitRow = (row: string) =>
          row.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim())
        const header = splitRow(line)
        const dataRows: string[][] = []
        i += 2
        while (i < lines.length && lines[i].trim().startsWith('|') && lines[i].trim().endsWith('|')) {
          dataRows.push(splitRow(lines[i]))
          i += 1
        }
        blocks.push({ kind: 'table', lines: [], rows: [header, ...dataRows] })
        continue
      }
    }
    // paragraph: 收集连续非空行直到下一个空行 / block 起头
    const paraLines: string[] = [line]
    i += 1
    while (
      i < lines.length
      && lines[i].trim() !== ''
      && !lines[i].startsWith('```')
      && !/^#{1,4}\s+/.test(lines[i])
      && !/^[-*]\s+/.test(lines[i])
      && !/^\d+\.\s+/.test(lines[i])
      && !(lines[i].trim().startsWith('|') && lines[i].trim().endsWith('|'))
    ) {
      paraLines.push(lines[i])
      i += 1
    }
    blocks.push({ kind: 'paragraph', lines: paraLines })
  }
  return blocks
}

function renderInline(text: string): ReactNode[] {
  // 不解析嵌套链接 / 嵌套 code — 顺序: code → link → bold → italic
  const nodes: ReactNode[] = []
  let buf = ''
  let i = 0
  let keyCounter = 0
  const pushBuf = () => {
    if (buf) {
      nodes.push(buf)
      buf = ''
    }
  }
  while (i < text.length) {
    // inline code: `xxx`
    if (text[i] === '`') {
      const end = text.indexOf('`', i + 1)
      if (end > i) {
        pushBuf()
        nodes.push(
          <code key={`c-${keyCounter++}`} className="rounded bg-muted/50 px-1 font-mono text-xs">
            {text.slice(i + 1, end)}
          </code>
        )
        i = end + 1
        continue
      }
    }
    // link: [text](url)
    if (text[i] === '[') {
      const closeBracket = text.indexOf(']', i + 1)
      if (closeBracket > i && text[closeBracket + 1] === '(') {
        const closeParen = text.indexOf(')', closeBracket + 2)
        if (closeParen > closeBracket) {
          const linkText = text.slice(i + 1, closeBracket)
          const linkUrl = text.slice(closeBracket + 2, closeParen)
          pushBuf()
          const safeHref = sanitizeLinkHref(linkUrl)
          if (safeHref !== null) {
            nodes.push(
              <a
                key={`l-${keyCounter++}`}
                href={safeHref}
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary underline"
              >
                {linkText}
              </a>
            )
          } else {
            // 危险协议 → 渲染为不可点击文本 (保留可读性, 不得引入 href)
            nodes.push(
              <span key={`l-${keyCounter++}`} className="text-muted-foreground" title="已过滤不安全链接">
                {linkText}
              </span>
            )
          }
          i = closeParen + 1
          continue
        }
      }
    }
    // bold: **xxx**
    if (text[i] === '*' && text[i + 1] === '*') {
      const end = text.indexOf('**', i + 2)
      if (end > i + 1) {
        pushBuf()
        nodes.push(<strong key={`b-${keyCounter++}`}>{text.slice(i + 2, end)}</strong>)
        i = end + 2
        continue
      }
    }
    // italic: *xxx* (单星)
    if (text[i] === '*' && text[i + 1] !== '*') {
      const end = text.indexOf('*', i + 1)
      if (end > i) {
        pushBuf()
        nodes.push(<em key={`i-${keyCounter++}`}>{text.slice(i + 1, end)}</em>)
        i = end + 1
        continue
      }
    }
    buf += text[i]
    i += 1
  }
  pushBuf()
  return nodes
}

/**
 * 协议白名单: 只允许 http / https / mailto 与"无协议相对路径" (e.g. /docs, ./foo,
 * ../bar). 任何带 javascript: / data: / vbscript: / file: 等危险协议 (含
 * 大小写混淆, 含前导空白) 全部拒掉. 返回 null → 调用方按"不可点击文本"
 * 渲染, 不得注入 href.
 *
 * 为什么不直接走 React 文本转义? 因为 `<a href="javascript:...">` 即便
 * 内部文本被 React 转义, href 属性的值仍会作为 URL 执行, 触发 XSS.
 */
export function sanitizeLinkHref(raw: string): string | null {
  if (typeof raw !== 'string') return null
  // 去掉前导空白 / 控制字符 — 攻击常用 ``\tjavascript:`` 等绕过.
  // 控制字符范围: U+0000..U+001F, U+007F, 加普通空白.
  const trimmed = raw.replace(/[\u0000-\u001F\u007F\s]+/g, '')
  if (!trimmed) return null
  const lower = trimmed.toLowerCase()
  // 先看是否有显式 scheme (含 :// 或 : 后面是 alpha-only)
  const schemeMatch = lower.match(/^([a-z][a-z0-9+.-]*):/i)
  if (schemeMatch) {
    const scheme = schemeMatch[1]
    if (
      scheme === 'http'
      || scheme === 'https'
      || scheme === 'mailto'
    ) {
      return trimmed
    }
    // 其它显式 scheme 一律拒 (javascript / data / vbscript / file / ...)
    return null
  }
  // 相对路径 / 协议相对 / 锚 / query
  if (
    trimmed.startsWith('/')
    || trimmed.startsWith('./')
    || trimmed.startsWith('../')
    || trimmed.startsWith('#')
    || trimmed.startsWith('?')
  ) {
    return trimmed
  }
  // 其它裸 token (如 "docs" "example.com") — 视作相对路径, 允许
  // 浏览器解析为当前页面相对路径, 不构成 XSS 风险
  if (/^[a-zA-Z0-9._~%!$&'()*+,;=:@\/?#-]+$/.test(trimmed)) {
    return trimmed
  }
  return null
}

export function MarkdownView({ content }: MarkdownViewProps) {
  if (!content) return null
  let blocks: Block[]
  try {
    blocks = parseMarkdown(content)
  } catch {
    // 解析失败: 兜底渲染为单段纯文本
    return <p className="whitespace-pre-wrap text-surface-foreground">{content}</p>
  }
  if (blocks.length === 0) {
    return <p className="whitespace-pre-wrap text-surface-foreground">{content}</p>
  }
  return (
    <div className="space-y-2 text-surface-foreground">
      {blocks.map((block, idx) => {
        if (block.kind === 'heading') {
          const level = block.level ?? 1
          const className =
            level === 1
              ? 'text-base font-semibold'
              : level === 2
                ? 'text-sm font-semibold'
                : 'text-sm font-medium'
          return (
            <p key={idx} className={className}>
              {renderInline(block.lines[0] ?? '')}
            </p>
          )
        }
        if (block.kind === 'paragraph') {
          return (
            <p key={idx} className="whitespace-pre-wrap">
              {block.lines.map((line, lineIdx) => (
                <Fragment key={lineIdx}>
                  {lineIdx > 0 ? <br /> : null}
                  {renderInline(line)}
                </Fragment>
              ))}
            </p>
          )
        }
        if (block.kind === 'ul') {
          return (
            <ul key={idx} className="list-disc pl-5 space-y-1">
              {block.lines.map((item, itemIdx) => (
                <li key={itemIdx}>{renderInline(item)}</li>
              ))}
            </ul>
          )
        }
        if (block.kind === 'ol') {
          return (
            <ol key={idx} className="list-decimal pl-5 space-y-1">
              {block.lines.map((item, itemIdx) => (
                <li key={itemIdx}>
                  {item.split('\n').map((line, lineIdx) => (
                    <Fragment key={lineIdx}>
                      {lineIdx > 0 ? <br /> : null}
                      {renderInline(line)}
                    </Fragment>
                  ))}
                </li>
              ))}
            </ol>
          )
        }
        if (block.kind === 'code') {
          return (
            <pre
              key={idx}
              className="max-h-48 overflow-auto rounded bg-muted/50 p-2 font-mono text-xs whitespace-pre-wrap"
            >
              {block.lines.join('\n')}
            </pre>
          )
        }
        if (block.kind === 'table' && block.rows && block.rows.length > 0) {
          const [header, ...rows] = block.rows
          return (
            <table key={idx} className="w-full border-collapse text-xs">
              <thead>
                <tr>
                  {header.map((cell, cellIdx) => (
                    <th
                      key={cellIdx}
                      className="border border-border/70 bg-muted/40 px-2 py-1 text-left font-medium"
                    >
                      {renderInline(cell)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row, rowIdx) => (
                  <tr key={rowIdx}>
                    {row.map((cell, cellIdx) => (
                      <td
                        key={cellIdx}
                        className="border border-border/70 px-2 py-1 align-top"
                      >
                        {renderInline(cell)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )
        }
        return null
      })}
    </div>
  )
}
