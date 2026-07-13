import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { createAuthService, type AuthUser } from '@/services/auth-service'
import { abortAuthenticatedRequests } from '@/services/http-client'

type AuthState = 'loading' | 'uninitialized' | 'anonymous' | 'authenticated'

interface AuthContextValue {
  state: AuthState
  user: AuthUser | null
  initialize(username: string, password: string): Promise<void>
  login(username: string, password: string): Promise<void>
  logout(): Promise<void>
  changePassword(currentPassword: string, newPassword: string): Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient()
  const mock = import.meta.env.VITE_API_MODE === 'mock'
  const [state, setState] = useState<AuthState>(mock ? 'authenticated' : 'loading')
  const [user, setUser] = useState<AuthUser | null>(mock ? {
    id: 'mock-admin', username: 'Admin', role: 'admin', can_access_adult: true, is_enabled: true,
  } : null)
  const service = useMemo(() => createAuthService(), [])

  useEffect(() => {
    if (mock) return
    void service.status().then(async ({ initialized }) => {
      if (!initialized) {
        queryClient.clear()
        return setState('uninitialized')
      }
      try {
        const result = await service.me()
        queryClient.clear()
        setUser(result.user)
        setState('authenticated')
      } catch {
        queryClient.clear()
        setState('anonymous')
      }
    }).catch(() => {
      queryClient.clear()
      setState('anonymous')
    })
  }, [mock, queryClient, service])

  useEffect(() => {
    if (mock) return
    const unauthorized = () => {
      abortAuthenticatedRequests()
      queryClient.clear()
      setUser(null)
      setState('anonymous')
    }
    window.addEventListener('media-pilot:unauthorized', unauthorized)
    return () => window.removeEventListener('media-pilot:unauthorized', unauthorized)
  }, [mock, queryClient])

  const value: AuthContextValue = {
    state,
    user,
    async initialize(username, password) {
      const result = await service.initialize(username, password)
      queryClient.clear()
      setUser(result.user); setState('authenticated')
    },
    async login(username, password) {
      const result = await service.login(username, password)
      queryClient.clear()
      setUser(result.user); setState('authenticated')
    },
    async logout() {
      try {
        await service.logout()
      } catch {
        // 服务端会话可能已经过期或网络不可用；本地退出仍必须完成。
      }
      abortAuthenticatedRequests(); queryClient.clear(); setUser(null); setState('anonymous')
    },
    async changePassword(currentPassword, newPassword) {
      await service.changePassword(currentPassword, newPassword)
      abortAuthenticatedRequests()
      queryClient.clear()
      setUser(null); setState('anonymous')
    },
  }
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used within AuthProvider')
  return value
}
