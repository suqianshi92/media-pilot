import { useState } from 'react'
import { Outlet } from 'react-router-dom'
import { TopBar } from './top-bar'
import { SidebarNav } from './sidebar-nav'

export function AppShell() {
  const [sidebarOpen, setSidebarOpen] = useState(false)

  return (
    <div className="flex min-h-screen flex-col bg-background">
      <TopBar onToggleSidebar={() => setSidebarOpen((v) => !v)} />

      <div className="flex flex-1">
        <SidebarNav
          open={sidebarOpen}
          onClose={() => setSidebarOpen(false)}
        />

        <main className="flex-1 overflow-auto p-6" data-testid="main-content">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
