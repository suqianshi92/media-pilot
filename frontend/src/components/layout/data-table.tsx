import {
  flexRender,
  getCoreRowModel,
  getPaginationRowModel,
  useReactTable,
  type ColumnDef,
} from '@tanstack/react-table'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'

interface DataTableProps<T> {
  columns: ColumnDef<T, any>[]
  data: T[]
  pageSize?: number
  renderMobileCard: (item: T) => React.ReactNode
  tableMeta?: Record<string, unknown>
  /**
   * 关闭内部分页. 当数据已由调用方 (例如后端分页) 控制 page 切
   * 换时, 设为 true 避免 DataTable 在传入的 data 上再本地分页, 否
   * 则会出现"假装本地分页是全局分页"的假象. 配合外层维护的
   * pagination 控件使用.
   */
  disablePagination?: boolean
}

export function DataTable<T>({
  columns,
  data,
  pageSize = 10,
  renderMobileCard,
  tableMeta,
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

  return (
    <div>
      {/* 桌面表格 */}
      <div className="hidden md:block overflow-x-auto rounded-lg border border-border">
        <table className="w-full text-sm">
          <thead>
            {table.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id} className="border-b border-border bg-muted/50">
                {headerGroup.headers.map((header) => (
                  <th
                    key={header.id}
                    className="px-4 py-3 text-left text-xs font-medium text-muted-foreground whitespace-nowrap"
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
                  <td key={cell.id} className="px-4 py-3 whitespace-nowrap">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 窄屏卡片 */}
      <div className="md:hidden grid gap-3">
        {table.getRowModel().rows.map((row) => renderMobileCard(row.original))}
      </div>

      {/* 分页 (内部分页关闭时不渲染) */}
      {!disablePagination && (
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
