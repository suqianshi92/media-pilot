import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { AgentRunStatusBadge, agentRunStatusColorMap } from './agent-run-status-badge'

describe('AgentRunStatusBadge', () => {
  afterEach(cleanup)
  it('renders active status with blue color classes', () => {
    render(<AgentRunStatusBadge runStatus="active" label="Agent 处理中" />)
    const badge = screen.getByTestId('agent-run-status-badge')
    expect(badge).toHaveAttribute('data-run-status', 'active')
    expect(badge.className).toContain('bg-blue-500/15')
    expect(badge.className).toContain('text-blue-200')
    expect(badge).toHaveTextContent('Agent 处理中')
  })

  it('renders waiting_user status with amber color classes', () => {
    render(<AgentRunStatusBadge runStatus="waiting_user" label="等待用户" />)
    const badge = screen.getByTestId('agent-run-status-badge')
    expect(badge).toHaveAttribute('data-run-status', 'waiting_user')
    expect(badge.className).toContain('bg-amber-500/20')
    expect(badge.className).toContain('text-amber-100')
    expect(badge).toHaveTextContent('等待用户')
  })

  it('renders failed status with rose color classes', () => {
    render(<AgentRunStatusBadge runStatus="failed" label="失败" />)
    const badge = screen.getByTestId('agent-run-status-badge')
    expect(badge).toHaveAttribute('data-run-status', 'failed')
    expect(badge.className).toContain('bg-rose-500/20')
    expect(badge.className).toContain('text-rose-100')
    expect(badge).toHaveTextContent('失败')
  })

  it('renders completed status with emerald color classes', () => {
    render(<AgentRunStatusBadge runStatus="completed" label="完成" />)
    const badge = screen.getByTestId('agent-run-status-badge')
    expect(badge).toHaveAttribute('data-run-status', 'completed')
    expect(badge.className).toContain('bg-emerald-500/15')
    expect(badge.className).toContain('text-emerald-200')
    expect(badge).toHaveTextContent('完成')
  })

  it('renders none status with muted text color', () => {
    render(<AgentRunStatusBadge runStatus="none" label="无" />)
    const badge = screen.getByTestId('agent-run-status-badge')
    expect(badge).toHaveAttribute('data-run-status', 'none')
    expect(badge.className).toContain('text-muted-foreground')
    expect(badge).toHaveTextContent('无')
  })

  it('falls back to runStatus as label when no label provided', () => {
    render(<AgentRunStatusBadge runStatus="active" />)
    expect(screen.getByTestId('agent-run-status-badge')).toHaveTextContent('active')
  })

  it('exports a color map keyed by AgentRunStatus', () => {
    expect(agentRunStatusColorMap.active).toContain('blue')
    expect(agentRunStatusColorMap.waiting_user).toContain('amber')
    expect(agentRunStatusColorMap.failed).toContain('rose')
    expect(agentRunStatusColorMap.completed).toContain('emerald')
    expect(agentRunStatusColorMap.none).toContain('muted')
  })
})
