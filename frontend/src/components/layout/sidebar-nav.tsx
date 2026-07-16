import { NavLink } from 'react-router-dom'
import { KeyRound, LayoutDashboard, ListTodo, Search, Settings, Upload, UserRoundCog, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/auth-context'

interface SidebarNavProps {
  open: boolean
  onClose: () => void
}

const navKeys = [
  { to: '/', icon: LayoutDashboard, labelKey: 'nav.home' },
  { to: '/discovery', icon: Search, labelKey: 'nav.resourceSearch' },
  { to: '/manual-upload', icon: Upload, labelKey: 'nav.manualUpload' },
  { to: '/tasks', icon: ListTodo, labelKey: 'nav.taskList' },
  { to: '/account', icon: KeyRound, labelKey: 'nav.account' },
  { to: '/users', icon: UserRoundCog, labelKey: 'nav.users', adminOnly: true },
  { to: '/settings', icon: Settings, labelKey: 'nav.settings' },
]

export function SidebarNav({ open, onClose }: SidebarNavProps) {
  const { t } = useTranslation()
  const { user } = useAuth()
  const visibleItems = navKeys.filter((item) => (!('adminOnly' in item) && item.to !== '/settings') || user?.role === 'admin')

  return (
    <>
      {open && (
        <div
          className="fixed inset-0 z-30 bg-black/20 md:hidden"
          onClick={onClose}
          data-testid="sidebar-overlay"
        />
      )}

      <aside
        className={`
          fixed left-0 top-14 z-30 h-[calc(100vh-3.5rem)] w-56
          border-r border-border bg-surface transition-transform overflow-y-auto
          md:sticky md:block
          ${open ? 'translate-x-0' : '-translate-x-full md:translate-x-0'}
        `}
        data-testid="sidebar-nav"
      >
        <div className="flex items-center justify-between px-4 py-3 md:hidden">
          <span className="text-sm font-medium text-muted-foreground">导航</span>
          <button onClick={onClose} className="text-muted-foreground hover:text-surface-foreground">
            <X className="h-4 w-4" />
          </button>
        </div>

        <nav className="flex flex-col gap-1 px-2 py-3">
          {visibleItems.map((item) => {
            const label = t(item.labelKey)
            return (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === '/'}
                onClick={onClose}
                className={({ isActive }) =>
                  `flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors
                  ${isActive
                    ? 'bg-primary/10 text-primary'
                    : 'text-muted-foreground hover:bg-muted hover:text-surface-foreground'
                  }`
                }
                data-testid={`nav-${label}`}
              >
                <item.icon className="h-4 w-4" />
                {label}
              </NavLink>
            )
          })}
        </nav>
      </aside>
    </>
  )
}
