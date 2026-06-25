import {
  createBrowserRouter,
  createMemoryRouter,
  type RouteObject,
} from 'react-router-dom'

import { AppLayout } from './layout'
import { DashboardPage } from '@/pages/dashboard-page'
import { DiscoveryPage } from '@/pages/discovery-page'
import { DownloadDetailPage } from '@/pages/download-detail-page'
import { ManualUploadPage } from '@/pages/manual-upload-page'
import { NotFoundPage } from '@/pages/not-found-page'
import { SettingsPage } from '@/pages/settings-page'
import { TaskDetailPage } from '@/pages/task-detail-page'
import { TaskListPage } from '@/pages/task-list-page'

const routes: RouteObject[] = [
  {
    path: '/',
    element: <AppLayout />,
    children: [
      {
        index: true,
        element: <DashboardPage />,
      },
      {
        path: 'tasks',
        element: <TaskListPage />,
      },
      {
        path: 'tasks/:taskId',
        element: <TaskDetailPage />,
      },
      {
        path: 'discovery',
        element: <DiscoveryPage />,
      },
      {
        path: 'manual-upload',
        element: <ManualUploadPage />,
      },
      {
        path: 'downloads/:downloadId',
        element: <DownloadDetailPage />,
      },
      {
        path: 'settings',
        element: <SettingsPage />,
      },
      {
        path: '*',
        element: <NotFoundPage />,
      },
    ],
  },
]

interface CreateAppRouterOptions {
  initialEntries?: string[]
}

export function createAppRouter(options?: CreateAppRouterOptions) {
  if (options?.initialEntries) {
    return createMemoryRouter(routes, { initialEntries: options.initialEntries })
  }

  return createBrowserRouter(routes, { basename: '/app' })
}
