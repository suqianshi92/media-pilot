import { render, screen } from '@testing-library/react'
import { AlertTriangle, Inbox, RotateCw, SearchX } from 'lucide-react'
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup } from '@testing-library/react'

import {
  ConfidenceBadge,
  EmptyState,
  ErrorState,
  IconButton,
  MessageCallout,
  PageShell,
  RiskCallout,
  SkeletonBlock,
  StatusBadge,
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from './shared-ui'

describe('shared app UI', () => {
  afterEach(cleanup)
  it('renders the page shell with heading, description and actions', () => {
    render(
      <PageShell
        title="任务列表"
        description="用于浏览与筛选任务"
        actions={<button type="button">刷新</button>}
      >
        <div>列表内容</div>
      </PageShell>,
    )

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('任务列表')
    expect(screen.getByText('用于浏览与筛选任务')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '刷新' })).toBeInTheDocument()
    expect(screen.getByText('列表内容')).toBeInTheDocument()
  })

  it('renders status and confidence badges in Chinese', () => {
    render(
      <div>
        <StatusBadge status="waiting_user" />
        <ConfidenceBadge level="medium" value={0.62} />
      </div>,
    )

    expect(screen.getByText('等待用户处理')).toBeInTheDocument()
    expect(screen.getByText('中等 0.62')).toBeInTheDocument()
  })

  it('renders risk, message and empty states', () => {
    render(
      <div>
        <RiskCallout title="需要注意" description="当前候选存在年份冲突" icon={AlertTriangle} />
        <MessageCallout title="提示" description="后台状态正在模拟刷新" />
        <EmptyState
          title="暂无任务"
          description="当前筛选条件下没有可展示的任务。"
          icon={Inbox}
          action={<button type="button">重置筛选</button>}
        />
      </div>,
    )

    expect(screen.getByText('需要注意')).toBeInTheDocument()
    expect(screen.getByText('当前候选存在年份冲突')).toBeInTheDocument()
    expect(screen.getByText('提示')).toBeInTheDocument()
    expect(screen.getByText('后台状态正在模拟刷新')).toBeInTheDocument()
    expect(screen.getByText('暂无任务')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重置筛选' })).toBeInTheDocument()
  })

  it('renders icon buttons, tooltip, skeleton and error state', async () => {
    render(
      <div>
        <TooltipProvider>
          <Tooltip defaultOpen>
            <TooltipTrigger asChild>
              <IconButton label="刷新列表" icon={RotateCw} />
            </TooltipTrigger>
            <TooltipContent>刷新列表</TooltipContent>
          </Tooltip>
        </TooltipProvider>
        <SkeletonBlock className="h-8 w-24" />
        <ErrorState
          title="加载失败"
          description="当前无法读取任务详情。"
          icon={SearchX}
          action={<button type="button">重试</button>}
        />
      </div>,
    )

    const button = screen.getByRole('button', { name: '刷新列表' })
    expect(button).toBeInTheDocument()

    expect(await screen.findByRole('tooltip')).toHaveTextContent('刷新列表')

    expect(screen.getByTestId('skeleton-block')).toBeInTheDocument()
    expect(screen.getByText('加载失败')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument()
  })

  it('renders StatusBadge(agent_running) with blue color classes', () => {
    // 回归: 详情页 agent_running StatusBadge 必须是蓝色, 和共享
    // AgentRunStatusBadge(active) 视觉一致, 跨详情页 / 任务列表 / 首页
    // 三处不再出现"详情页蓝, 列表灰"的差异.
    const { container } = render(<StatusBadge status="agent_running" />)
    const badge = container.querySelector('span.inline-flex')
    expect(badge).not.toBeNull()
    expect(badge?.className).toContain('bg-blue-500/15')
    expect(badge?.className).toContain('text-blue-200')
    expect(badge?.className).toContain('border-blue-400/45')
  })
})
