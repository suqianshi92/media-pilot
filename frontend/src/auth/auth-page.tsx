import { FormEvent, useState } from 'react'
import { Navigate, useSearchParams } from 'react-router-dom'

import { useAuth } from '@/auth/auth-context'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

export function AuthPage({ mode }: { mode: 'login' | 'initialize' }) {
  const auth = useAuth()
  const [searchParams] = useSearchParams()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState('')
  if (auth.state === 'loading') return <div className="grid min-h-screen place-items-center">正在加载…</div>
  const requested = searchParams.get('next')
  const destination = requested?.startsWith('/') && !requested.startsWith('//') ? requested : '/'
  if (auth.state === 'authenticated') return <Navigate to={mode === 'login' ? destination : '/'} replace />
  if (mode === 'login' && auth.state === 'uninitialized') return <Navigate to="/initialize" replace />
  if (mode === 'initialize' && auth.state === 'anonymous') return <Navigate to="/login" replace />

  async function submit(event: FormEvent) {
    event.preventDefault(); setError('')
    if (mode === 'initialize' && password !== confirmPassword) {
      setError('两次输入的密码不一致')
      return
    }
    try {
      if (mode === 'initialize') await auth.initialize(username, password)
      else await auth.login(username, password)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '请求失败')
    }
  }

  return <main className="grid min-h-screen place-items-center bg-background p-6">
    <form onSubmit={submit} className="grid w-full max-w-sm gap-4 rounded-lg border border-border bg-surface p-6">
      <h1 className={`text-xl font-semibold ${mode === 'initialize' ? 'text-center' : ''}`}>
        {mode === 'initialize' ? '创建初始管理员' : '登录 Media Pilot'}
      </h1>
      <div className="grid gap-2">
        <label htmlFor="auth-username" className="text-sm font-medium">用户名</label>
        <Input id="auth-username" value={username} onChange={(e) => setUsername(e.target.value)} required />
      </div>
      <div className="grid gap-2">
        <label htmlFor="auth-password" className="text-sm font-medium">密码</label>
        <Input
          id="auth-password"
          type="password"
          minLength={8}
          maxLength={128}
          aria-describedby={mode === 'initialize' ? 'password-requirement' : undefined}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        {mode === 'initialize' && <p id="password-requirement" className="text-xs text-muted-foreground">密码至少 8 个字符</p>}
      </div>
      {mode === 'initialize' && <div className="grid gap-2">
        <label htmlFor="auth-confirm-password" className="text-sm font-medium">确认密码</label>
        <Input
          id="auth-confirm-password"
          type="password"
          minLength={8}
          maxLength={128}
          value={confirmPassword}
          onChange={(e) => setConfirmPassword(e.target.value)}
          required
        />
      </div>}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <Button type="submit">{mode === 'initialize' ? '创建并登录' : '登录'}</Button>
    </form>
  </main>
}
