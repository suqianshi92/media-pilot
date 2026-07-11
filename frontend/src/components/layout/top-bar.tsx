import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { LogOut, Menu, RefreshCw, Sun, Moon, Monitor } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { LANGUAGE_KEY } from '@/i18n'
import { useAuth } from '@/auth/auth-context'

const markUrl = `${import.meta.env.BASE_URL}media-pilot-mark.svg`

interface TopBarProps {
  onToggleSidebar: () => void
}

export function TopBar({ onToggleSidebar }: TopBarProps) {
  const { t } = useTranslation()
  const auth = useAuth()

  return (
    <header className="sticky top-0 z-40 flex h-14 items-center gap-4 border-b border-border bg-surface px-4">
      <Button
        variant="ghost"
        size="icon"
        className="md:hidden"
        onClick={onToggleSidebar}
        data-testid="sidebar-toggle"
      >
        <Menu className="h-5 w-5" />
      </Button>

      <div className="flex items-center gap-2 font-semibold text-sm">
        <img src={markUrl} alt="" className="h-6 w-6" aria-hidden="true" />
        <span className="hidden sm:inline">{t('topBar.title')}</span>
      </div>

      <div className="flex-1" />

      <Button
        variant="ghost"
        size="icon"
        onClick={() => window.location.reload()}
        data-testid="refresh-btn"
        title={t('topBar.refresh')}
      >
        <RefreshCw className="h-4 w-4" />
      </Button>

      <ThemeToggle />
      <LanguageToggle />
      <span className="hidden text-xs text-muted-foreground sm:inline">{auth.user?.username}</span>
      <Button
        variant="ghost"
        size="icon"
        onClick={() => void auth.logout()}
        title="退出登录"
        aria-label="退出登录"
      >
        <LogOut className="h-4 w-4" />
      </Button>
    </header>
  )
}

// ── theme helpers ──

const THEME_KEY = 'media-pilot-theme'

function readTheme(): string {
  try { return localStorage.getItem(THEME_KEY) || 'system' } catch { return 'system' }
}

function writeTheme(theme: string) {
  try { localStorage.setItem(THEME_KEY, theme) } catch { /* noop */ }
}

function readLanguage(): string {
  try { return localStorage.getItem(LANGUAGE_KEY) || 'zh' } catch { return 'zh' }
}

function writeLanguage(lang: string) {
  try { localStorage.setItem(LANGUAGE_KEY, lang) } catch { /* noop */ }
}

function applyTheme(theme: string) {
  const root = document.documentElement
  root.classList.remove('light', 'dark')
  if (theme === 'light') {
    root.classList.add('light')
  } else if (theme === 'dark') {
    root.classList.add('dark')
  } else {
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
    root.classList.add(prefersDark ? 'dark' : 'light')
  }
}

// ── ThemeToggle with state ──

const THEME_CYCLE = ['light', 'dark', 'system'] as const

function ThemeToggle() {
  const { t } = useTranslation()
  const [theme, setThemeState] = useState(readTheme)

  const themeTitleMap: Record<string, string> = {
    light: t('topBar.themeLight'),
    dark: t('topBar.themeDark'),
    system: t('topBar.themeSystem'),
  }

  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={() => {
        const idx = THEME_CYCLE.indexOf(theme as typeof THEME_CYCLE[number])
        const next = THEME_CYCLE[(idx + 1) % 3]
        writeTheme(next)
        applyTheme(next)
        setThemeState(next)
      }}
      data-testid="theme-toggle"
      title={themeTitleMap[theme]}
    >
      {theme === 'light' ? <Sun className="h-4 w-4" /> :
       theme === 'dark' ? <Moon className="h-4 w-4" /> :
       <Monitor className="h-4 w-4" />}
    </Button>
  )
}

// ── LanguageToggle with i18n ──

function LanguageToggle() {
  const { i18n } = useTranslation()
  const [lang, setLangState] = useState(readLanguage)

  const switchTo = (next: string) => {
    writeLanguage(next)
    i18n.changeLanguage(next)
    setLangState(next)
  }

  return (
    <div className="flex items-center rounded border border-border text-xs" data-testid="language-toggle">
      <button
        onClick={() => switchTo('zh')}
        className={`px-2 py-1 rounded-l transition-colors ${
          lang === 'zh'
            ? 'bg-primary/10 text-primary font-medium'
            : 'text-muted-foreground hover:text-surface-foreground'
        }`}
        data-testid="lang-zh"
      >
        中文
      </button>
      <button
        onClick={() => switchTo('en')}
        className={`px-2 py-1 rounded-r transition-colors ${
          lang === 'en'
            ? 'bg-primary/10 text-primary font-medium'
            : 'text-muted-foreground hover:text-surface-foreground'
        }`}
        data-testid="lang-en"
      >
        English
      </button>
    </div>
  )
}

// Initial theme application on load
if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
  applyTheme(readTheme())
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (readTheme() === 'system') applyTheme('system')
  })
}

export { readTheme, writeTheme, readLanguage, writeLanguage, applyTheme }
