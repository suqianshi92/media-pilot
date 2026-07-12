import { FormEvent, useState } from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createColumnHelper } from '@tanstack/react-table'
import { useTranslation } from 'react-i18next'

import { DataTable } from '@/components/layout/data-table'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { Input } from '@/components/ui/input'
import { useToast } from '@/components/shared/toast'
import { createUserService, type ManagedUser } from '@/services/user-service'

type UserService = ReturnType<typeof createUserService>
const columnHelper = createColumnHelper<ManagedUser>()

export function UserManagementPage({ service = createUserService() }: { service?: UserService }) {
  const { t } = useTranslation()
  const { showToast } = useToast()
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [adult, setAdult] = useState(false)
  const [resetTarget, setResetTarget] = useState<ManagedUser | null>(null)
  const [resetPassword, setResetPassword] = useState('')
  const users = useQuery({
    queryKey: ['users', page, pageSize],
    queryFn: () => service.list(page, pageSize),
    placeholderData: keepPreviousData,
  })
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['users'] })
  const createMutation = useMutation({
    mutationFn: service.create,
    onSuccess: () => {
      setUsername(''); setPassword(''); setAdult(false); void refresh()
      showToast(t('userManagement.createSuccess'))
    },
    onError: (error) => showToast(error.message || t('userManagement.operationFailed'), 'error'),
  })
  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: { is_enabled?: boolean; can_access_adult?: boolean } }) => service.update(id, data),
    onSuccess: () => { void refresh(); showToast(t('userManagement.updateSuccess')) },
    onError: (error) => showToast(error.message || t('userManagement.operationFailed'), 'error'),
  })
  const resetMutation = useMutation({
    mutationFn: ({ id, value }: { id: string; value: string }) => service.resetPassword(id, value),
    onSuccess: () => {
      setResetTarget(null); setResetPassword('')
      showToast(t('userManagement.resetSuccess'))
    },
    onError: (error) => showToast(error.message || t('userManagement.operationFailed'), 'error'),
  })

  const renderActions = (user: ManagedUser, withTestId = true) => {
      const testId = withTestId ? { 'data-testid': `user-${user.id}-actions` } : {}
      if (user.role === 'admin') return <span {...testId}>{t('userManagement.protected')}</span>
      return <div className="flex flex-wrap gap-2" {...testId}>
        <Button size="sm" variant="secondary" disabled={updateMutation.isPending} onClick={() => updateMutation.mutate({ id: user.id, data: { is_enabled: !user.is_enabled } })}>{t(user.is_enabled ? 'userManagement.disable' : 'userManagement.enable')}</Button>
        <Button size="sm" variant="secondary" disabled={updateMutation.isPending} onClick={() => updateMutation.mutate({ id: user.id, data: { can_access_adult: !user.can_access_adult } })}>{t(user.can_access_adult ? 'userManagement.disableAdult' : 'userManagement.enableAdult')}</Button>
        <Button size="sm" variant="secondary" onClick={() => setResetTarget(user)}>{t('userManagement.resetPassword')}</Button>
      </div>
  }
  const columns = [
    columnHelper.accessor('username', { header: t('userManagement.username') }),
    columnHelper.accessor('role', { header: t('userManagement.role'), cell: (info) => t(info.getValue() === 'admin' ? 'userManagement.admin' : 'userManagement.user') }),
    columnHelper.accessor('is_enabled', { header: t('userManagement.status'), cell: (info) => t(info.getValue() ? 'userManagement.enabled' : 'userManagement.disabled') }),
    columnHelper.accessor('can_access_adult', { header: t('userManagement.adultAccess'), cell: (info) => t(info.getValue() ? 'userManagement.allowed' : 'userManagement.denied') }),
    columnHelper.accessor('created_at', { header: t('userManagement.createdAt'), cell: (info) => new Date(info.getValue()).toLocaleString() }),
    columnHelper.display({ id: 'actions', header: t('userManagement.actions'), cell: ({ row }) => renderActions(row.original) }),
  ]

  function submit(event: FormEvent) {
    event.preventDefault()
    createMutation.mutate({ username, password, can_access_adult: adult })
  }
  const items = users.data?.data.items ?? []
  return <div className="grid gap-6">
    <div><h1 className="text-xl font-semibold">{t('userManagement.title')}</h1><p className="text-sm text-muted-foreground">{t('userManagement.description')}</p></div>
    <form onSubmit={submit} className="flex flex-wrap items-end gap-3 rounded-lg border border-border bg-surface p-4">
      <label className="grid gap-1 text-sm">{t('userManagement.username')}<Input value={username} onChange={(e) => setUsername(e.target.value)} required /></label>
      <label className="grid gap-1 text-sm">{t('userManagement.password')}<Input type="password" minLength={8} maxLength={128} value={password} onChange={(e) => setPassword(e.target.value)} required /></label>
      <label className="flex h-10 items-center gap-2 text-sm"><input type="checkbox" checked={adult} onChange={(e) => setAdult(e.target.checked)} />{t('userManagement.adultAccess')}</label>
      <Button type="submit" disabled={createMutation.isPending}>{t('userManagement.create')}</Button>
    </form>
    {users.isError ? <p role="alert">用户列表加载失败</p> : <DataTable
      columns={columns} data={items} disablePagination renderMobileCard={(user) => <div className="grid gap-3 rounded border p-3"><strong>{user.username}</strong>{renderActions(user, false)}</div>}
      tableClassName="min-w-[900px]" serverPagination={{ page, pageSize, total: users.data?.meta.total ?? 0, pageSizeOptions: [10, 20, 50, 100], pending: users.isFetching, onPageChange: setPage, onPageSizeChange: (value) => { setPage(1); setPageSize(value) } }}
    />}
    <ConfirmDialog open={resetTarget !== null} title={t('userManagement.resetPassword')} description={t('userManagement.resetDescription', { username: resetTarget?.username })} confirmLabel={t('common.confirm')} cancelLabel={t('common.cancel')} loading={resetMutation.isPending} onCancel={() => { setResetTarget(null); setResetPassword('') }} onConfirm={() => resetTarget && resetPassword.length >= 8 && resetMutation.mutate({ id: resetTarget.id, value: resetPassword })}>
      <Input aria-label={t('userManagement.newPassword')} type="password" minLength={8} maxLength={128} value={resetPassword} onChange={(event) => setResetPassword(event.target.value)} placeholder={t('userManagement.passwordRule')} />
    </ConfirmDialog>
  </div>
}
