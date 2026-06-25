/** Source cleanup UI 行为测试 — i18n key 存在 + 摘要函数. */

import { describe, expect, it } from 'vitest'

import en from '@/i18n/locales/en.json'
import zh from '@/i18n/locales/zh.json'

// 摘要函数 — 与 agent-panel.tsx 中的 summarizeSourceCleanup 同语义.
function summarizeSourceCleanup(output: unknown): string {
  if (!output || typeof output !== 'object') return ''
  const o = output as Record<string, unknown>
  const action = o.action
  if (action === 'kept') return '源文件按策略保留'
  if (action === 'trashed') {
    const target = typeof o.trash_target === 'string' ? o.trash_target : ''
    return target ? `源文件已移入回收区: ${target}` : '源文件已移入回收区'
  }
  if (action === 'trash_failed') {
    const reason = typeof o.reason === 'string' ? o.reason : ''
    return reason ? `清理失败: ${reason}` : '清理失败'
  }
  if (o.decision_type === 'source_cleanup_action') {
    return '已请求用户决策 (保留 / 移入回收区 / 进入删除预检)'
  }
  if (action === 'preflight_refused') {
    const reason = typeof o.reason === 'string' ? o.reason : ''
    return reason ? `预检拒绝, 已降级为请求用户决策: ${reason}` : '预检拒绝, 已降级为请求用户决策'
  }
  return ''
}

describe('i18n source cleanup option keys', () => {
  it('zh.json defines the three source_cleanup_action options', () => {
    const cleanup = (zh as any).agent.sourceCleanupAction
    expect(cleanup).toBeTruthy()
    expect(cleanup.keepInput).toBeTruthy()
    expect(cleanup.trashInput).toBeTruthy()
    expect(cleanup.deleteInput).toBeTruthy()
    expect(cleanup.keepInputDesc).toBeTruthy()
    expect(cleanup.trashInputDesc).toBeTruthy()
    expect(cleanup.deleteInputDesc).toBeTruthy()
  })

  it('en.json defines the three source_cleanup_action options', () => {
    const cleanup = (en as any).agent.sourceCleanupAction
    expect(cleanup).toBeTruthy()
    expect(cleanup.keepInput).toBeTruthy()
    expect(cleanup.trashInput).toBeTruthy()
    expect(cleanup.deleteInput).toBeTruthy()
    expect(cleanup.keepInputDesc).toBeTruthy()
    expect(cleanup.trashInputDesc).toBeTruthy()
    expect(cleanup.deleteInputDesc).toBeTruthy()
  })
})

describe('summarizeSourceCleanup', () => {
  it('returns friendly text for kept action', () => {
    expect(summarizeSourceCleanup({ action: 'kept' })).toContain('保留')
  })

  it('returns target path for trashed action', () => {
    const s = summarizeSourceCleanup({
      action: 'trashed',
      trash_target: '/trash/movie.mkv',
    })
    expect(s).toContain('回收区')
    expect(s).toContain('/trash/movie.mkv')
  })

  it('returns reason for trash_failed action', () => {
    const s = summarizeSourceCleanup({
      action: 'trash_failed',
      reason: 'refuse_protected_root',
    })
    expect(s).toContain('refuse_protected_root')
  })

  it('returns decision prompt for source_cleanup_action', () => {
    const s = summarizeSourceCleanup({
      decision_type: 'source_cleanup_action',
      decision_id: 'dec-1',
    })
    expect(s).toContain('用户决策')
    expect(s).toContain('保留')
    expect(s).toContain('回收区')
  })

  it('returns downgrade hint for preflight_refused', () => {
    const s = summarizeSourceCleanup({
      action: 'preflight_refused',
      reason: 'trash_dir_not_configured',
    })
    expect(s).toContain('trash_dir_not_configured')
  })

  it('returns empty string for unknown output', () => {
    expect(summarizeSourceCleanup({})).toBe('')
    expect(summarizeSourceCleanup(null)).toBe('')
  })
})
