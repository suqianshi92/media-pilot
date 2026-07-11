import {
  createBrowserRouter,
  createMemoryRouter,
  Navigate,
  Outlet,
  useLocation,
  type RouteObject,
} from 'react-router-dom'

import { AppLayout } from './layout'
import { AuthProvider, useAuth } from '@/auth/auth-context'
import { AuthPage } from '@/auth/auth-page'
import { DashboardPage } from '@/pages/dashboard-page'
import { DiscoveryPage } from '@/pages/discovery-page'
import { DownloadDetailPage } from '@/pages/download-detail-page'
import { ManualUploadPage } from '@/pages/manual-upload-page'
import { NotFoundPage } from '@/pages/not-found-page'
import { SettingsPage } from '@/pages/settings-page'
import { TaskDetailPage } from '@/pages/task-detail-page'
import { TaskListPage } from '@/pages/task-list-page'
import { UserManagementPage } from '@/pages/user-management-page'
import { AccountPage } from '@/pages/account-page'

function ProtectedRoute() {
  const auth = useAuth()
  const location = useLocation()
  if (auth.state === 'loading') return <div className="grid min-h-screen place-items-center">正在加载…</div>
  if (auth.state === 'uninitialized') return <Navigate to="/initialize" replace />
  if (auth.state === 'anonymous') {
    const next = `${location.pathname}${location.search}`
    return <Navigate to={`/login?next=${encodeURIComponent(next)}`} replace />
  }
  return <Outlet />
}

function AuthRoot() {
  return <AuthProvider><Outlet /></AuthProvider>
}

function AdminRoute() {
  const { user } = useAuth()
  if (user?.role !== 'admin') return <div role="alert" className="p-6">无权访问此页面</div>
  return <Outlet />
}

function TaskListRoute() {
  const { user } = useAuth()
  return <TaskListPage showOwner={user?.role === 'admin'} />
}

const routes: RouteObject[] = [
  {
    element: <AuthRoot />,
    children: [
      { path: '/login', element: <AuthPage mode="login" /> },
      { path: '/initialize', element: <AuthPage mode="initialize" /> },
      { element: <ProtectedRoute />, children: [{ path: '/', element: <AppLayout />, children: [
      {
        index: true,
        element: <DashboardPage />,
      },
      {
        path: 'tasks',
        element: <TaskListRoute />,
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
        path: 'account',
        element: <AccountPage />,
      },
      { element: <AdminRoute />, children: [
        { path: 'settings', element: <SettingsPage /> },
        { path: 'users', element: <UserManagementPage /> },
      ] },
      {
        path: '*',
        element: <NotFoundPage />,
      },
      ]}] },
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
