import { createContext, useContext, useEffect, useMemo, useState } from 'react'

import { createAuthService, type AuthUser } from '@/services/auth-service'

type AuthState = 'loading' | 'uninitialized' | 'anonymous' | 'authenticated'

interface AuthContextValue {
  state: AuthState
  user: AuthUser | null
  initialize(username: string, password: string): Promise<void>
  login(username: string, password: string): Promise<void>
  logout(): Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const mock = import.meta.env.VITE_API_MODE === 'mock'
  const [state, setState] = useState<AuthState>(mock ? 'authenticated' : 'loading')
  const [user, setUser] = useState<AuthUser | null>(mock ? {
    id: 'mock-admin', username: 'Admin', role: 'admin', can_access_adult: true, is_enabled: true,
  } : null)
  const service = useMemo(() => createAuthService(), [])

  useEffect(() => {
    if (mock) return
    void service.status().then(async ({ initialized }) => {
      if (!initialized) return setState('uninitialized')
      try {
        const result = await service.me()
        setUser(result.user)
        setState('authenticated')
      } catch {
        setState('anonymous')
      }
    }).catch(() => setState('anonymous'))
  }, [mock, service])

  useEffect(() => {
    if (mock) return
    const unauthorized = () => { setUser(null); setState('anonymous') }
    window.addEventListener('media-pilot:unauthorized', unauthorized)
    return () => window.removeEventListener('media-pilot:unauthorized', unauthorized)
  }, [mock])

  const value: AuthContextValue = {
    state,
    user,
    async initialize(username, password) {
      const result = await service.initialize(username, password)
      setUser(result.user); setState('authenticated')
    },
    async login(username, password) {
      const result = await service.login(username, password)
      setUser(result.user); setState('authenticated')
    },
    async logout() {
      await service.logout(); setUser(null); setState('anonymous')
    },
  }
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used within AuthProvider')
  return value
}
