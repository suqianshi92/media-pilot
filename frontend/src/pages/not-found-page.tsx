import { Link } from 'react-router-dom'

export function NotFoundPage() {
  return (
    <section className="grid gap-4">
      <div className="grid gap-1">
        <h1 className="text-3xl font-semibold tracking-normal">页面不存在</h1>
        <p className="text-sm text-muted-foreground">请返回任务列表继续操作。</p>
      </div>
      <div>
        <Link className="text-sm font-medium text-primary underline-offset-4 hover:underline" to="/">
          返回任务列表
        </Link>
      </div>
    </section>
  )
}
