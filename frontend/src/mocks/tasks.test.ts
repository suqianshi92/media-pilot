import { describe, expect, it } from 'vitest'

import { mockTaskDetails } from './tasks'
import { createMockTaskService } from './service'

function collectNonSnakeCaseKeys(value: unknown, path = 'root'): string[] {
  if (Array.isArray(value)) {
    return value.flatMap((item, index) => collectNonSnakeCaseKeys(item, `${path}[${index}]`))
  }

  if (!value || typeof value !== 'object') {
    return []
  }

  return Object.entries(value as Record<string, unknown>).flatMap(([key, nestedValue]) => {
    const failures: string[] = []
    if (!/^[a-z0-9_]+$/.test(key)) {
      failures.push(`${path}.${key}`)
    }
    return failures.concat(collectNonSnakeCaseKeys(nestedValue, `${path}.${key}`))
  })
}

describe('mock task data naming', () => {
  it('uses snake_case keys in exported mock task details', () => {
    expect(collectNonSnakeCaseKeys(mockTaskDetails)).toEqual([])
  })

  it('uses snake_case keys in mock service responses', async () => {
    const service = createMockTaskService()
    const listResponse = await service.listTasks({ status: 'waiting_user' })
    const detailResponse = await service.getTaskDetail('task-multiple-candidates')

    expect(collectNonSnakeCaseKeys(listResponse)).toEqual([])
    expect(collectNonSnakeCaseKeys(detailResponse)).toEqual([])
  })
})
