# 前端开发

前端工程位于 `frontend/`，技术栈为 React 18 + Vite 5 + TypeScript + Tailwind CSS + shadcn/ui + TanStack Query。

## 本地开发

```bash
cd frontend
npm install
npm run dev
```

默认访问 `http://localhost:5173`。默认 mock 模式不需要后端。

## 对接真实后端

```bash
VITE_API_MODE=real VITE_API_BASE_URL=http://localhost:8000 npm run dev
```

- `VITE_API_MODE`：`mock` 或 `real`
- `VITE_API_BASE_URL`：后端地址，默认同源

## 构建与检查

```bash
npm run build
npm run preview
npm run typecheck
npm run lint
npm run test
```
