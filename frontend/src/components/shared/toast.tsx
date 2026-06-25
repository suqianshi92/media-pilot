import { createContext, useCallback, useContext, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { X, CheckCircle, AlertCircle, Info } from 'lucide-react'

export interface Toast {
  id: string
  message: string
  variant: 'success' | 'error' | 'info'
}

interface ToastContextValue {
  toasts: Toast[]
  showToast: (message: string, variant?: Toast['variant']) => void
}

const ToastContext = createContext<ToastContextValue>({
  toasts: [],
  showToast: () => {},
})

export function useToast() {
  return useContext(ToastContext)
}

let _nextId = 0

export function ToastProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation()
  const [toasts, setToasts] = useState<Toast[]>([])

  const showToast = useCallback(
    (message: string, variant: Toast['variant'] = 'success') => {
      const id = `toast-${++_nextId}`
      setToasts((prev) => [...prev, { id, message, variant }])
      setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== id))
      }, 5000)
    },
    [],
  )

  const dismiss = useCallback((id: string) => {
    setToasts((prev) => prev.filter((toast) => toast.id !== id))
  }, [])

  const icon = (variant: Toast['variant']) => {
    switch (variant) {
      case 'success':
        return <CheckCircle className="h-4 w-4 text-green-600" />
      case 'error':
        return <AlertCircle className="h-4 w-4 text-destructive" />
      case 'info':
        return <Info className="h-4 w-4 text-blue-600" />
    }
  }

  return (
    <ToastContext.Provider value={{ toasts, showToast }}>
      {children}

      {/* 右上角浮窗容器 */}
      <div className="fixed top-4 right-4 z-50 flex flex-col gap-2 max-w-lg pointer-events-none">
        {toasts.map((toastItem) => (
          <div
            key={toastItem.id}
            className={`pointer-events-auto flex items-start gap-2 rounded-md border-2 px-6 py-4 shadow-xl text-sm ${
              toastItem.variant === 'error'
                ? 'border-destructive/70 bg-red-50/95 dark:border-destructive/80 dark:bg-red-950/90 text-destructive'
                : toastItem.variant === 'info'
                  ? 'border-blue-400/80 bg-blue-50/95 dark:border-blue-500/70 dark:bg-blue-950/90 text-blue-800 dark:text-blue-100'
                  : 'border-green-400/80 bg-green-50/95 dark:border-green-500/70 dark:bg-green-950/90 text-green-800 dark:text-green-100'
            }`}
          >
            <span className="shrink-0 mt-0.5">{icon(toastItem.variant)}</span>
            <span className="flex-1 min-w-0">{toastItem.message}</span>
            <button
              onClick={() => dismiss(toastItem.id)}
              className="shrink-0 ml-2 text-muted-foreground hover:text-foreground"
              aria-label={t('common.close')}
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}
