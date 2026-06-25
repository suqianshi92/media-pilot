import * as React from 'react'

interface DropdownMenuContextValue {
  open: boolean
  setOpen: (open: boolean) => void
  triggerRef: React.RefObject<HTMLDivElement | null>
}

const DropdownMenuContext = React.createContext<DropdownMenuContextValue>({
  open: false,
  setOpen: () => {},
  triggerRef: { current: null },
})

export function DropdownMenu({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = React.useState(false)
  const triggerRef = React.useRef<HTMLDivElement>(null)
  return (
    <DropdownMenuContext.Provider value={{ open, setOpen, triggerRef }}>
      <div className="relative inline-block" ref={triggerRef}>
        {children}
      </div>
    </DropdownMenuContext.Provider>
  )
}

export function DropdownMenuTrigger({
  children,
  asChild,
}: {
  children: React.ReactNode
  asChild?: boolean
}) {
  const { open, setOpen } = React.useContext(DropdownMenuContext)
  const toggle = () => setOpen(!open)
  if (asChild && React.isValidElement(children)) {
    return React.cloneElement(children as React.ReactElement<{ onClick?: React.MouseEventHandler }>, {
      onClick: (e: React.MouseEvent) => {
        (children as React.ReactElement<{ onClick?: React.MouseEventHandler }>).props.onClick?.(e)
        toggle()
      },
    })
  }
  return (
    <button type="button" onClick={toggle}>
      {children}
    </button>
  )
}

export function DropdownMenuContent({
  children,
  align = 'start',
}: {
  children: React.ReactNode
  align?: 'start' | 'end'
}) {
  const { open, setOpen, triggerRef } = React.useContext(DropdownMenuContext)
  const ref = React.useRef<HTMLDivElement>(null)

  React.useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      const target = e.target as Node
      if (ref.current && !ref.current.contains(target) && !triggerRef.current?.contains(target)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open, setOpen, triggerRef])

  if (!open) return null

  return (
    <div
      ref={ref}
      className={`absolute z-50 mt-1 min-w-[140px] rounded-md border border-border bg-background p-1 shadow-md ${
        align === 'end' ? 'right-0' : 'left-0'
      }`}
    >
      {children}
    </div>
  )
}

export function DropdownMenuItem({
  children,
  onClick,
  disabled,
  className,
}: {
  children: React.ReactNode
  onClick?: () => void
  disabled?: boolean
  className?: string
}) {
  const { setOpen } = React.useContext(DropdownMenuContext)
  return (
    <button
      type="button"
      className={`flex w-full items-center rounded-sm px-2 py-1.5 text-sm transition-colors ${
        disabled
          ? 'cursor-not-allowed opacity-50'
          : 'hover:bg-muted'
      } ${className ?? ''}`}
      disabled={disabled}
      onClick={() => {
        onClick?.()
        setOpen(false)
      }}
    >
      {children}
    </button>
  )
}
