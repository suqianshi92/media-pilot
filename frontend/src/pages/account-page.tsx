import { FormEvent, useState } from 'react'

import { useAuth } from '@/auth/auth-context'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

export function AccountPage() {
  const auth = useAuth()
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault(); setError('')
    if (newPassword !== confirmation) return setError('两次输入的新密码不一致')
    setPending(true)
    try {
      await auth.changePassword(currentPassword, newPassword)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '修改密码失败')
      setPending(false)
    }
  }

  return <div className="mx-auto grid max-w-lg gap-6">
    <div><h1 className="text-xl font-semibold">账号设置</h1><p className="text-sm text-muted-foreground">当前用户：{auth.user?.username}</p></div>
    <form onSubmit={submit} className="grid gap-4 rounded-lg border border-border bg-surface p-6">
      <label className="grid gap-1 text-sm">当前密码<Input type="password" minLength={8} maxLength={128} value={currentPassword} onChange={(e) => setCurrentPassword(e.target.value)} required /></label>
      <label className="grid gap-1 text-sm">新密码<Input type="password" minLength={8} maxLength={128} value={newPassword} onChange={(e) => setNewPassword(e.target.value)} required /></label>
      <label className="grid gap-1 text-sm">确认新密码<Input type="password" minLength={8} maxLength={128} value={confirmation} onChange={(e) => setConfirmation(e.target.value)} required /></label>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <Button type="submit" disabled={pending}>修改密码并重新登录</Button>
    </form>
  </div>
}
