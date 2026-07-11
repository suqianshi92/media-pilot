import { FormEvent, useMemo, useState } from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createColumnHelper } from '@tanstack/react-table'

import { DataTable } from '@/components/layout/data-table'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { createUserService, type ManagedUser } from '@/services/user-service'

type UserService = ReturnType<typeof createUserService>
const columnHelper = createColumnHelper<ManagedUser>()

export function UserManagementPage({ service = createUserService() }: { service?: UserService }) {
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [adult, setAdult] = useState(false)
  const users = useQuery({
    queryKey: ['users', page, pageSize],
    queryFn: () => service.list(page, pageSize),
    placeholderData: keepPreviousData,
  })
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['users'] })
  const createMutation = useMutation({ mutationFn: service.create, onSuccess: () => {
    setUsername(''); setPassword(''); setAdult(false); void refresh()
  } })
  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: { is_enabled?: boolean; can_access_adult?: boolean } }) => service.update(id, data),
    onSuccess: refresh,
  })
  const resetMutation = useMutation({
    mutationFn: ({ id, value }: { id: string; value: string }) => service.resetPassword(id, value),
  })

  const columns = useMemo(() => [
    columnHelper.accessor('username', { header: '用户名' }),
    columnHelper.accessor('role', { header: '角色', cell: (info) => info.getValue() === 'admin' ? '管理员' : '普通用户' }),
    columnHelper.accessor('is_enabled', { header: '状态', cell: (info) => info.getValue() ? '启用' : '停用' }),
    columnHelper.accessor('can_access_adult', { header: '成人权限', cell: (info) => info.getValue() ? '允许' : '禁止' }),
    columnHelper.accessor('created_at', { header: '创建时间', cell: (info) => new Date(info.getValue()).toLocaleString() }),
    columnHelper.display({ id: 'actions', header: '操作', cell: ({ row }) => {
      const user = row.original
      if (user.role === 'admin') return <span data-testid={`user-${user.id}-actions`}>受保护</span>
      return <div className="flex gap-2" data-testid={`user-${user.id}-actions`}>
        <Button size="sm" variant="secondary" onClick={() => updateMutation.mutate({ id: user.id, data: { is_enabled: !user.is_enabled } })}>{user.is_enabled ? '停用' : '启用'}</Button>
        <Button size="sm" variant="secondary" onClick={() => updateMutation.mutate({ id: user.id, data: { can_access_adult: !user.can_access_adult } })}>{user.can_access_adult ? '关闭成人权限' : '开启成人权限'}</Button>
        <Button size="sm" variant="secondary" onClick={() => {
          const value = window.prompt('输入新密码（8–128 个字符）')
          if (value) resetMutation.mutate({ id: user.id, value })
        }}>重置密码</Button>
      </div>
    } }),
  ], [resetMutation, updateMutation])

  function submit(event: FormEvent) {
    event.preventDefault()
    createMutation.mutate({ username, password, can_access_adult: adult })
  }
  const items = users.data?.data.items ?? []
  return <div className="grid gap-6">
    <div><h1 className="text-xl font-semibold">用户管理</h1><p className="text-sm text-muted-foreground">创建和管理 Media Pilot 本地用户</p></div>
    <form onSubmit={submit} className="flex flex-wrap items-end gap-3 rounded-lg border border-border bg-surface p-4">
      <label className="grid gap-1 text-sm">用户名<Input value={username} onChange={(e) => setUsername(e.target.value)} required /></label>
      <label className="grid gap-1 text-sm">密码<Input type="password" minLength={8} maxLength={128} value={password} onChange={(e) => setPassword(e.target.value)} required /></label>
      <label className="flex h-10 items-center gap-2 text-sm"><input type="checkbox" checked={adult} onChange={(e) => setAdult(e.target.checked)} />成人权限</label>
      <Button type="submit" disabled={createMutation.isPending}>创建普通用户</Button>
    </form>
    {users.isError ? <p role="alert">用户列表加载失败</p> : <DataTable
      columns={columns} data={items} disablePagination renderMobileCard={(user) => <div className="rounded border p-3">{user.username}</div>}
      tableClassName="min-w-[900px]" serverPagination={{ page, pageSize, total: users.data?.meta.total ?? 0, pageSizeOptions: [10, 20, 50, 100], pending: users.isFetching, onPageChange: setPage, onPageSizeChange: (value) => { setPage(1); setPageSize(value) } }}
    />}
  </div>
}
