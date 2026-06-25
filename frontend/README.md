# Media Pilot Frontend

当前目录是 `modernize-ui-and-async-task-api` 变更的独立前端工程。

当前阶段目标：

- 建立 React + Vite + TypeScript 前端骨架
- 接入 Tailwind CSS、基础样式 token、基础 UI 组件和 TanStack Query
- 使用纯前端页面与 mock 数据完成中文操作台原型
- 暂不对接真实后端 API

## 运行环境

- Node `v22.22.2`
- npm `10.9.7`

## 当前技术栈

- React 18
- Vite 5
- TypeScript
- Tailwind CSS
- TanStack Query
- lucide-react
- shadcn 风格基础组件

## 本地启动

```bash
cd frontend
npm install
npm run dev
```

默认开发地址：

- `http://127.0.0.1:5173`

## 当前脚本

```bash
npm run dev
npm run lint
npm run typecheck
npm run test
npm run build
```

含义：

- `dev`：启动前端开发服务器
- `lint`：执行 ESLint 检查
- `typecheck`：执行 TypeScript 类型检查
- `test`：执行 Vitest 最小测试
- `build`：执行类型检查并构建生产产物

## 当前模式

当前仍是 **mock / prototype 模式**：

- 页面不依赖真实 `/api/v1`
- 任务列表、详情、人工确认、重搜、状态刷新后续会先以 mock DTO 和 mock service 实现
- 真实 API 对接放在后续任务阶段

## 约束

- 当前阶段不修改后端 workflow
- 只有涉及静态产物部署、容器挂载、权限或运行时依赖时，才做 Docker Compose 验证
- 每完成一个小任务，优先执行对应前端检查，再做本地 Git 提交
