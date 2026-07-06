import { describe, expect, it, afterEach } from 'vitest'
import { cleanup, render } from '@testing-library/react'

import { MarkdownView, sanitizeLinkHref } from '@/components/agent/markdown-view'

afterEach(() => {
  cleanup()
})

describe('MarkdownView — 段落', () => {
  it('单行段落渲染为 <p>', () => {
    const { container } = render(<MarkdownView content="hello world" />)
    expect(container.querySelectorAll('p').length).toBeGreaterThan(0)
    expect(container.textContent).toContain('hello world')
  })

  it('多行段落用 <br> 换行 (单段内)', () => {
    const { container } = render(<MarkdownView content={'line 1\nline 2'} />)
    expect(container.querySelectorAll('br').length).toBe(1)
  })
})

describe('MarkdownView — 标题', () => {
  it('# 渲染为 heading 1', () => {
    const { container } = render(<MarkdownView content="# 标题" />)
    const p = container.querySelector('p.text-base')
    expect(p).toBeTruthy()
    expect(p!.textContent).toBe('标题')
  })

  it('## 渲染为 heading 2', () => {
    const { container } = render(<MarkdownView content="## 副标题" />)
    const p = container.querySelector('p.text-sm.font-semibold')
    expect(p).toBeTruthy()
    expect(p!.textContent).toBe('副标题')
  })
})

describe('MarkdownView — 列表', () => {
  it('无序列表', () => {
    const { container } = render(<MarkdownView content={'- a\n- b\n- c'} />)
    const lis = container.querySelectorAll('li')
    expect(lis.length).toBe(3)
    expect(lis[0].textContent).toBe('a')
  })

  it('有序列表', () => {
    const { container } = render(<MarkdownView content={'1. 一\n2. 二'} />)
    const lis = container.querySelectorAll('li')
    expect(lis.length).toBe(2)
    expect(lis[0].textContent).toBe('一')
  })

  it('有序列表条目允许缩进续行, 不拆成多个从 1 开始的列表', () => {
    const md = [
      '1. **赴汤蹈火**（2016）',
      '   - 推荐理由：现代西部片。',
      '   - 推荐搜索词：赴汤蹈火 2016',
      '',
      '1. **边境杀手**（2015）',
      '   - 推荐理由：冷峻犯罪片。',
      '   - 推荐搜索词：边境杀手 2015',
    ].join('\n')
    const { container } = render(<MarkdownView content={md} />)
    const orderedLists = container.querySelectorAll('ol')
    const lis = container.querySelectorAll('li')

    expect(orderedLists.length).toBe(1)
    expect(lis.length).toBe(2)
    expect(lis[0].textContent).toContain('赴汤蹈火')
    expect(lis[0].textContent).toContain('推荐搜索词：赴汤蹈火 2016')
    expect(lis[1].textContent).toContain('边境杀手')
  })
})

describe('MarkdownView — 表格', () => {
  it('基本表格 (头 + 分隔 + 行)', () => {
    const md = [
      '| 列1 | 列2 |',
      '| --- | --- |',
      '| a   | b   |',
      '| c   | d   |',
    ].join('\n')
    const { container } = render(<MarkdownView content={md} />)
    const table = container.querySelector('table')
    expect(table).toBeTruthy()
    expect(container.querySelectorAll('th').length).toBe(2)
    expect(container.querySelectorAll('tbody tr').length).toBe(2)
    expect(container.querySelector('tbody tr td')!.textContent).toBe('a')
  })
})

describe('MarkdownView — 行内 code / 粗体 / 斜体 / 链接', () => {
  it('行内 code 用 <code> 包裹', () => {
    const { container } = render(<MarkdownView content="use `npm install`" />)
    const code = container.querySelector('code')
    expect(code).toBeTruthy()
    expect(code!.textContent).toBe('npm install')
  })

  it('粗体 **xxx** 渲染为 <strong>', () => {
    const { container } = render(<MarkdownView content="this is **bold**" />)
    const strong = container.querySelector('strong')
    expect(strong).toBeTruthy()
    expect(strong!.textContent).toBe('bold')
  })

  it('斜体 *xxx* 渲染为 <em>', () => {
    const { container } = render(<MarkdownView content="this is *italic*" />)
    const em = container.querySelector('em')
    expect(em).toBeTruthy()
    expect(em!.textContent).toBe('italic')
  })

  it('链接 [text](url) 渲染为 <a target=_blank rel=noopener>', () => {
    const { container } = render(<MarkdownView content="see [docs](https://example.com)" />)
    const a = container.querySelector('a')
    expect(a).toBeTruthy()
    expect(a!.getAttribute('href')).toBe('https://example.com')
    expect(a!.getAttribute('target')).toBe('_blank')
    expect(a!.getAttribute('rel')).toBe('noopener noreferrer')
  })
})

describe('MarkdownView — code block', () => {
  it('```...``` 渲染为 <pre>', () => {
    const md = ['```bash', 'ls -la', 'pwd', '```'].join('\n')
    const { container } = render(<MarkdownView content={md} />)
    const pre = container.querySelector('pre')
    expect(pre).toBeTruthy()
    expect(pre!.textContent).toContain('ls -la')
    expect(pre!.textContent).toContain('pwd')
  })
})

describe('MarkdownView — XSS 防护 (无 dangerouslySetInnerHTML)', () => {
  it('<script> 注入原样显示为字符, 不执行', () => {
    const { container } = render(
      <MarkdownView content={'<script>alert(1)</script>'} />
    )
    // 渲染结果中不应该有真正的 <script> 元素 (只应有文本)
    expect(container.querySelector('script')).toBeNull()
    // 文本必须原样保留
    expect(container.textContent).toContain('<script>')
    expect(container.textContent).toContain('</script>')
  })

  it('任意 HTML 标签原样显示', () => {
    const { container } = render(
      <MarkdownView content={'<img src=x onerror=alert(1)>'} />
    )
    expect(container.querySelector('img')).toBeNull()
    expect(container.textContent).toContain('<img')
  })
})

describe('MarkdownView — 边界 / 兜底', () => {
  it('空字符串渲染为空', () => {
    const { container } = render(<MarkdownView content="" />)
    expect(container.textContent).toBe('')
  })

  it('混合 markdown 块: heading + list + paragraph 各自正确渲染', () => {
    const md = [
      '# 标题',
      '',
      '- 项目 A',
      '- 项目 B',
      '',
      '结尾段.',
    ].join('\n')
    const { container } = render(<MarkdownView content={md} />)
    expect(container.querySelector('p.text-base')).toBeTruthy()
    expect(container.querySelectorAll('li').length).toBe(2)
    expect(container.textContent).toContain('结尾段.')
  })
})

// ---------------------------------------------------------------------------
// sanitizeLinkHref — 协议白名单 / XSS 防护
// ---------------------------------------------------------------------------

describe('sanitizeLinkHref', () => {
  it('http / https 通过', () => {
    expect(sanitizeLinkHref('http://example.com')).toBe('http://example.com')
    expect(sanitizeLinkHref('https://example.com/x?y=1#z')).toBe(
      'https://example.com/x?y=1#z'
    )
  })

  it('mailto 通过', () => {
    expect(sanitizeLinkHref('mailto:foo@bar.com')).toBe('mailto:foo@bar.com')
  })

  it('相对路径 / 锚 / query 通过', () => {
    expect(sanitizeLinkHref('/docs/page')).toBe('/docs/page')
    expect(sanitizeLinkHref('./page')).toBe('./page')
    expect(sanitizeLinkHref('../sibling')).toBe('../sibling')
    expect(sanitizeLinkHref('#anchor')).toBe('#anchor')
    expect(sanitizeLinkHref('?q=1')).toBe('?q=1')
  })

  it('裸 token (相对路径) 通过', () => {
    expect(sanitizeLinkHref('docs/page')).toBe('docs/page')
    expect(sanitizeLinkHref('example.com/foo')).toBe('example.com/foo')
  })

  it('javascript: 拒 (含大小写 / 前导空白 / 编码绕过)', () => {
    expect(sanitizeLinkHref('javascript:alert(1)')).toBeNull()
    expect(sanitizeLinkHref('JavaScript:alert(1)')).toBeNull()
    expect(sanitizeLinkHref('\tjavascript:alert(1)')).toBeNull()
    expect(sanitizeLinkHref('  JaVaScRiPt:alert(1)')).toBeNull()
    expect(sanitizeLinkHref('java\tscript:alert(1)')).toBeNull()
  })

  it('data: / vbscript: / file: 拒', () => {
    expect(sanitizeLinkHref('data:text/html,<script>alert(1)</script>')).toBeNull()
    expect(sanitizeLinkHref('vbscript:msgbox(1)')).toBeNull()
    expect(sanitizeLinkHref('file:///etc/passwd')).toBeNull()
  })

  it('空串 / 纯空白 / 非字符串 → null', () => {
    expect(sanitizeLinkHref('')).toBeNull()
    expect(sanitizeLinkHref('   ')).toBeNull()
    expect(sanitizeLinkHref(null as unknown as string)).toBeNull()
    expect(sanitizeLinkHref(undefined as unknown as string)).toBeNull()
  })

  it('嵌入 NUL / 控制字符绕过 (U+0000..U+001F, U+007F) 仍被拒', () => {
    // 攻击者常用 ``\x00javascript:alert(1)`` 之类绕过朴素 trim.
    // 修复要求: sanitizeLinkHref 必须在剥离前导空白 / 控制字符后再判断 scheme.
    expect(sanitizeLinkHref(String.fromCharCode(0x00) + 'javascript:alert(1)')).toBeNull()
    expect(sanitizeLinkHref(String.fromCharCode(0x01) + 'javascript:alert(1)')).toBeNull()
    expect(sanitizeLinkHref(String.fromCharCode(0x1f) + 'javascript:alert(1)')).toBeNull()
    expect(sanitizeLinkHref('java' + String.fromCharCode(0x00) + 'script:alert(1)')).toBeNull()
    expect(sanitizeLinkHref('j' + String.fromCharCode(0x07) + 'avascript:alert(1)')).toBeNull()
    expect(sanitizeLinkHref(String.fromCharCode(0x7f) + 'javascript:alert(1)')).toBeNull()
  })
})

describe('markdown-view 源码 — 不得嵌入真实 NUL / 控制字符 (回归保护)', () => {
  // 历史教训: 之前 ``sanitizeLinkHref`` 的控制字符正则 ([\x00-\x1f\x7f]) 直接
  // 写入了真实的 NUL / 0x1f 字节, 导致 .tsx 文件被 git 视为 binary, 阻碍
  // 后续 code review. 此处断言源码不再含 NUL / 其他控制字符, 强制使用
  // 可读的 Unicode escape 形式 (e.g. ``\x00``).
  it('不含 NUL (0x00) 字节', async () => {
    const fs = await import('node:fs/promises')
    const path = await import('node:path')
    const src = await fs.readFile(
      path.resolve(__dirname, '../markdown-view.tsx'),
      'utf-8'
    )
    expect(src).not.toContain(String.fromCharCode(0x00))
  })

  it('不含其他 ASCII 控制字符 (0x01..0x1F, 0x7F)', async () => {
    const fs = await import('node:fs/promises')
    const path = await import('node:path')
    const src = await fs.readFile(
      path.resolve(__dirname, '../markdown-view.tsx'),
      'utf-8'
    )
    const offenders: { code: number; idx: number }[] = []
    for (let i = 0; i < src.length; i += 1) {
      const code = src.charCodeAt(i)
      // 允许 0x09 (\t) / 0x0A (\n) / 0x0D (\r) — 它们是合法空白
      if (code === 0x09 || code === 0x0a || code === 0x0d) continue
      if (code < 0x20 || code === 0x7f) {
        offenders.push({ code, idx: i })
      }
    }
    expect(offenders).toEqual([])
  })
})

describe('MarkdownView — 危险链接不得注入 href', () => {
  it('javascript: 链接渲染为不可点击 span, 不出现 <a href="javascript:...">', () => {
    const { container } = render(
      <MarkdownView content={'[点我](javascript:alert(1))'} />
    )
    // 不应存在 <a> 元素 (因为 javascript: 被拒)
    expect(container.querySelector('a')).toBeNull()
    // 文本必须保留, 用户能看到 label
    expect(container.textContent).toContain('点我')
    // 兜底: span 渲染
    const span = container.querySelector('span[title]')
    expect(span).toBeTruthy()
  })

  it('data: 链接同样被拒', () => {
    const { container } = render(
      <MarkdownView content={'[pdf](data:text/html,<x>)'} />
    )
    expect(container.querySelector('a')).toBeNull()
    expect(container.textContent).toContain('pdf')
  })

  it('大小写混写的 javascript: 同样被拒', () => {
    const { container } = render(
      <MarkdownView content={'[evil](JaVaScRiPt:alert(1))'} />
    )
    expect(container.querySelector('a')).toBeNull()
  })

  it('带前导空白的 javascript: 同样被拒', () => {
    const { container } = render(
      <MarkdownView content={'[evil](   javascript:alert(1))'} />
    )
    expect(container.querySelector('a')).toBeNull()
  })

  it('正常 https 链接仍然渲染为 <a>', () => {
    const { container } = render(
      <MarkdownView content={'[docs](https://example.com)'} />
    )
    const a = container.querySelector('a')
    expect(a).toBeTruthy()
    expect(a!.getAttribute('href')).toBe('https://example.com')
  })
})
