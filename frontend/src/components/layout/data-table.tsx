import {
  flexRender,
  getCoreRowModel,
  getPaginationRowModel,
  useReactTable,
  type ColumnDef,
} from '@tanstack/react-table'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { ChevronLeft, ChevronRight } from 'lucide-react'

type PageItem = number | 'ellipsis-left' | 'ellipsis-right'

interface ServerPagination {
  page: number
  pageSize: number
  total: number
  pageSizeOptions: number[]
  pending?: boolean
  onPageChange: (page: number) => void
  onPageSizeChange: (pageSize: number) => void
}

interface DataTableProps<T> {
  columns: ColumnDef<T, any>[]
  data: T[]
  pageSize?: number
  renderMobileCard: (item: T) => React.ReactNode
  tableMeta?: Record<string, unknown>
  columnClassNames?: Record<string, string>
  className?: string
  tableContainerClassName?: string
  mobileContainerClassName?: string
  tableClassName?: string
  serverPagination?: ServerPagination
  /**
   * 关闭内部分页. 当数据已由调用方 (例如后端分页) 控制 page 切
   * 换时, 设为 true 避免 DataTable 在传入的 data 上再本地分页, 否
   * 则会出现"假装本地分页是全局分页"的假象. 配合外层维护的
   * pagination 控件使用.
   */
  disablePagination?: boolean
}

function buildPageItems(currentPage: number, totalPages: number): PageItem[] {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, index) => index + 1)
  }

  const windowSize = 5
  const half = Math.floor(windowSize / 2)
  let start = Math.max(2, currentPage - half)
  let end = Math.min(totalPages - 1, start + windowSize - 1)

  if (end - start + 1 < windowSize) {
    start = Math.max(2, end - windowSize + 1)
  }

  const items: PageItem[] = [1]
  if (start > 2) items.push('ellipsis-left')
  for (let page = start; page <= end; page += 1) items.push(page)
  if (end < totalPages - 1) items.push('ellipsis-right')
  items.push(totalPages)
  return items
}

export function DataTable<T>({
  columns,
  data,
  pageSize = 10,
  renderMobileCard,
  tableMeta,
  columnClassNames = {},
  className = '',
  tableContainerClassName = '',
  mobileContainerClassName = '',
  tableClassName = '',
  serverPagination,
  disablePagination = false,
}: DataTableProps<T>) {
  const { t } = useTranslation()
  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: disablePagination ? undefined : getPaginationRowModel(),
    initialState: disablePagination ? undefined : { pagination: { pageSize } },
    meta: tableMeta,
  })
  const serverTotalPages = serverPagination
    ? Math.max(1, Math.ceil(serverPagination.total / serverPagination.pageSize))
    : 1
  const serverPageItems = serverPagination
    ? buildPageItems(serverPagination.page, serverTotalPages)
    : []
  const getColumnClassName = (columnId: string) => columnClassNames[columnId] ?? ''

  return (
    <div className={className}>
      {/* 桌面表格 */}
      <div className={`hidden md:block overflow-auto rounded-lg border border-border ${tableContainerClassName}`}>
        <table className={`w-full text-sm ${tableClassName}`}>
          <thead>
            {table.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id} className="border-b border-border bg-muted/50">
                {headerGroup.headers.map((header) => (
                  <th
                    key={header.id}
                    className={`px-4 py-3 text-left text-xs font-medium text-muted-foreground whitespace-nowrap ${getColumnClassName(header.column.id)}`}
                  >
                    {header.isPlaceholder
                      ? null
                      : flexRender(header.column.columnDef.header, header.getContext())}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row) => (
              <tr
                key={row.id}
                className="border-b border-border last:border-b-0 hover:bg-muted/30 transition-colors"
              >
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} className={`px-4 py-3 whitespace-nowrap ${getColumnClassName(cell.column.id)}`}>
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 窄屏卡片 */}
      <div className={`md:hidden grid gap-3 ${mobileContainerClassName}`}>
        {table.getRowModel().rows.map((row) => (
          <div key={row.id}>{renderMobileCard(row.original)}</div>
        ))}
      </div>

      {serverPagination && serverPagination.total > 0 && (
        <div
          className={`mt-4 flex flex-wrap items-center justify-end gap-2 text-sm text-muted-foreground ${serverPagination.pending ? 'cursor-wait' : ''}`}
          aria-busy={serverPagination.pending}
        >
          <span>{t('taskList.total', { count: serverPagination.total })}</span>
          <div className="flex items-center gap-1">
            <Button
              variant="secondary"
              size="sm"
              aria-label={t('taskList.prev')}
              className="h-8 w-8 px-0"
              onClick={() => serverPagination.onPageChange(serverPagination.page - 1)}
              disabled={serverPagination.pending || serverPagination.page <= 1}
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            {serverPageItems.map((item) => {
              if (typeof item !== 'number') {
                return (
                  <span key={item} className="px-2 text-muted-foreground/70">
                    …
                  </span>
                )
              }
              const active = item === serverPagination.page
              return (
                <Button
                  key={item}
                  variant={active ? 'default' : 'secondary'}
                  size="sm"
                  className="h-8 min-w-8 px-2"
                  onClick={() => serverPagination.onPageChange(item)}
                  disabled={serverPagination.pending || active}
                  aria-current={active ? 'page' : undefined}
                >
                  {item}
                </Button>
              )
            })}
            <Button
              variant="secondary"
              size="sm"
              aria-label={t('taskList.next')}
              className="h-8 w-8 px-0"
              onClick={() => serverPagination.onPageChange(serverPagination.page + 1)}
              disabled={serverPagination.pending || serverPagination.page >= serverTotalPages}
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
          <label className="flex items-center gap-2">
            <span className="sr-only">{t('taskList.pageSize')}</span>
            <select
              aria-label={t('taskList.pageSize')}
              className="h-8 rounded-md border border-border bg-background px-2 text-sm text-foreground disabled:cursor-wait disabled:opacity-70"
              value={serverPagination.pageSize}
              onChange={(event) => serverPagination.onPageSizeChange(Number(event.target.value))}
              disabled={serverPagination.pending}
            >
              {serverPagination.pageSizeOptions.map((option) => (
                <option key={option} value={option}>
                  {t('taskList.pageSizeOption', { count: option })}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}

      {/* 分页 (内部分页关闭时不渲染) */}
      {!serverPagination && !disablePagination && (
        <div className="flex flex-col gap-3 mt-4 sm:flex-row sm:items-center sm:justify-between">
          <span className="text-sm text-muted-foreground">
            {t('taskList.page')} {table.getState().pagination.pageIndex + 1} / {table.getPageCount()}
            <span className="ml-2">
              {t('taskList.total', { count: data.length })}
            </span>
          </span>
          <div className="flex items-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => table.previousPage()}
              disabled={!table.getCanPreviousPage()}
            >
              {t('taskList.prev')}
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => table.nextPage()}
              disabled={!table.getCanNextPage()}
            >
              {t('taskList.next')}
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
