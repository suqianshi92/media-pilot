import { describe, expect, it } from 'vitest'

import { getStatusColorClass } from './task-labels'
import { agentRunStatusColorMap } from './agent-run-status-badge'

describe('getStatusColorClass', () => {
  it('returns the blue palette for agent_running matching detail page StatusBadge', () => {
    const classes = getStatusColorClass('agent_running')
    // 跟详情页 agentRunStatusColorMap.active 字面量严格一致
    expect(classes).toContain('bg-blue-500/15')
    expect(classes).toContain('text-blue-200')
    expect(classes).toContain('border-blue-400/45')
  })

  it('aligns agent_running class string literally with agentRunStatusColorMap.active', () => {
    // 防止未来某次改动让两个字符串漂移
    const expected = agentRunStatusColorMap.active
    const actual = getStatusColorClass('agent_running')
    expect(actual).toBe(expected)
  })

  it('does not return the gray fallback for agent_running', () => {
    const classes = getStatusColorClass('agent_running')
    expect(classes).not.toContain('bg-gray-100')
    expect(classes).not.toContain('bg-gray-')
  })

  it('keeps existing colors for other statuses (failed / library_import_complete / completed)', () => {
    // 不得被本次 agent_running 改动连带修改
    expect(getStatusColorClass('failed')).toContain('bg-red-100')
    expect(getStatusColorClass('agent_failed')).toContain('bg-red-100')
    expect(getStatusColorClass('sync_failed')).toContain('bg-red-100')
    expect(getStatusColorClass('library_import_complete')).toContain('bg-green-100')
    expect(getStatusColorClass('completed')).toContain('bg-yellow-100')
    expect(getStatusColorClass('waiting_user')).toContain('bg-yellow-100')
  })
})
