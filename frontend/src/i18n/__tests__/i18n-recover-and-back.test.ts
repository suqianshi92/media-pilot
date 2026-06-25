// ---------------------------------------------------------------------------
// i18n-recover-and-back.test.ts
//
// 覆盖 polish-agent-decision-actions-navigation-and-stuck-recovery 的 i18n
// 键: 卡住 Agent 恢复按钮 + 任务详情返回入口. 锁定:
//   - zh / en 都必须提供以下键, 不得硬编码在组件里:
//     agent.recoverStuckAgent       (恢复处理按钮)
//     agent.recovering              (恢复中状态)
//     agent.recoverStuckFailed      (恢复失败 toast)
//     taskWorkspace.backToTasks     (任务详情返回入口)
//
// 红色: 这些 key 暂未加入 zh.json / en.json — 测试必失败.
// ---------------------------------------------------------------------------

import { describe, expect, it } from 'vitest'

import i18n from '@/i18n'

const REQUIRED_KEYS = [
  'agent.recoverStuckAgent',
  'agent.recovering',
  'agent.recoverStuckFailed',
  'taskWorkspace.backToTasks',
] as const

describe('i18n keys for recover + back-to-tasks', () => {
  it.each(['zh', 'en'] as const)('%s 提供 recoverStuckAgent 键', (lang) => {
    i18n.changeLanguage(lang)
    const v = i18n.t('agent.recoverStuckAgent')
    expect(v).not.toBe('agent.recoverStuckAgent')
    expect(v.length).toBeGreaterThan(0)
  })

  it.each(['zh', 'en'] as const)('%s 提供 recovering 键', (lang) => {
    i18n.changeLanguage(lang)
    const v = i18n.t('agent.recovering')
    expect(v).not.toBe('agent.recovering')
    expect(v.length).toBeGreaterThan(0)
  })

  it.each(['zh', 'en'] as const)('%s 提供 recoverStuckFailed 键', (lang) => {
    i18n.changeLanguage(lang)
    const v = i18n.t('agent.recoverStuckFailed')
    expect(v).not.toBe('agent.recoverStuckFailed')
    expect(v.length).toBeGreaterThan(0)
  })

  it.each(['zh', 'en'] as const)('%s 提供 taskWorkspace.backToTasks 键', (lang) => {
    i18n.changeLanguage(lang)
    const v = i18n.t('taskWorkspace.backToTasks')
    expect(v).not.toBe('taskWorkspace.backToTasks')
    expect(v.length).toBeGreaterThan(0)
  })

  it('所有 required keys 在 zh / en 都有非空译文', () => {
    for (const lang of ['zh', 'en'] as const) {
      i18n.changeLanguage(lang)
      for (const key of REQUIRED_KEYS) {
        const v = i18n.t(key)
        expect(v, `${lang}.${key} 缺失或未翻译`).not.toBe(key)
        expect(v.length, `${lang}.${key} 译文为空`).toBeGreaterThan(0)
      }
    }
  })
})
