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
  const [error, setError] = useState('')
  if (auth.state === 'loading') return <div className="grid min-h-screen place-items-center">正在加载…</div>
  const requested = searchParams.get('next')
  const destination = requested?.startsWith('/') && !requested.startsWith('//') ? requested : '/'
  if (auth.state === 'authenticated') return <Navigate to={mode === 'login' ? destination : '/'} replace />
  if (mode === 'login' && auth.state === 'uninitialized') return <Navigate to="/initialize" replace />
  if (mode === 'initialize' && auth.state === 'anonymous') return <Navigate to="/login" replace />

  async function submit(event: FormEvent) {
    event.preventDefault(); setError('')
    try {
      if (mode === 'initialize') await auth.initialize(username, password)
      else await auth.login(username, password)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '请求失败')
    }
  }

  return <main className="grid min-h-screen place-items-center bg-background p-6">
    <form onSubmit={submit} className="grid w-full max-w-sm gap-4 rounded-lg border border-border bg-surface p-6">
      <h1 className="text-xl font-semibold">{mode === 'initialize' ? '创建初始管理员' : '登录 Media Pilot'}</h1>
      <Input aria-label="用户名" value={username} onChange={(e) => setUsername(e.target.value)} required />
      <Input aria-label="密码" type="password" minLength={8} maxLength={128} value={password} onChange={(e) => setPassword(e.target.value)} required />
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <Button type="submit">{mode === 'initialize' ? '创建并登录' : '登录'}</Button>
    </form>
  </main>
}
