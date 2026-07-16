# User Management UI Corrections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 固定侧边栏设置入口顺序，并把普通用户创建表单改为按钮触发弹窗。

**Architecture:** 侧边栏只调整静态菜单配置顺序。用户管理页复用现有 `ConfirmDialog`，保留当前 React Query mutation 和服务接口，只增加弹窗开关状态及成功/取消清理逻辑。

**Tech Stack:** React、TypeScript、React Query、React Testing Library、Vitest、Tailwind CSS。

## Global Constraints

- “设置”始终是侧边栏最后一个可见菜单项。
- 创建弹窗只包含用户名、密码和成人内容权限。
- 不增加确认密码；密码提示只显示“密码至少 8 个字符”。
- 创建失败保留弹窗和输入，创建成功关闭并清空。

---

### Task 1: 固定侧边栏菜单顺序

**Files:**
- Modify: `frontend/src/components/layout/sidebar-nav.tsx`
- Create: `frontend/src/components/layout/sidebar-nav.test.tsx`

- [x] 写管理员菜单顺序失败测试，断言“设置”为最后一个链接。
- [x] 运行该测试并确认失败。
- [x] 调整 `navKeys`，把用户管理放在设置之前。
- [x] 运行测试并确认通过。

### Task 2: 创建用户弹窗

**Files:**
- Modify: `frontend/src/pages/user-management-page.tsx`
- Modify: `frontend/src/pages/user-management-page.test.tsx`
- Modify: `frontend/src/i18n/locales/zh.json`
- Modify: `frontend/src/i18n/locales/en.json`

- [x] 写失败测试，覆盖默认隐藏、按钮打开、字段与提示、提交参数、成功关闭和列表刷新。
- [x] 运行测试并确认失败。
- [x] 用标题栏按钮和 `ConfirmDialog` 替换常驻创建表单。
- [x] 补充必要中英文弹窗文案。
- [x] 运行相关测试并确认通过。
- [x] 运行前端全量测试、ESLint 和生产构建。
- [x] 提交实现。

### Task 3: 调整创建文案和操作语义

**Files:**
- Modify: `frontend/src/components/ui/button.tsx`
- Modify: `frontend/src/pages/user-management-page.tsx`
- Modify: `frontend/src/pages/user-management-page.test.tsx`
- Modify: `frontend/src/i18n/locales/zh.json`

- [x] 写失败测试，覆盖“创建用户”文案、红色停用按钮和橙色成人权限按钮。
- [x] 增加 `destructive`、`warning` 按钮变体并应用到用户操作列。
- [x] 运行相关测试、前端全量测试、ESLint 和生产构建。
- [x] 提交实现。
