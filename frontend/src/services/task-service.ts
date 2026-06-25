/**
 * 任务服务 — 默认调用真实 /api/v1 后端; 仅当 VITE_API_MODE 显式设为
 * 'mock' 时才退到 mock service. 普通运行 (开发 / 生产) 不得静默
 * 落到 mock, 否则人工 E2E 实测会误判后端行为.
 *
 * mock service 仍保留, 用于测试组件依赖注入.
 */

import { createApiTaskService } from './api-client'
import { createMockTaskService } from '@/mocks/service'

const API_MODE = import.meta.env.VITE_API_MODE

export function createTaskService() {
  if (API_MODE === 'mock') {
    return createMockTaskService()
  }
  return createApiTaskService()
}

export type TaskService = ReturnType<typeof createApiTaskService>
