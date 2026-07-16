# Initial Admin Form Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为首次管理员初始化表单增加清晰字段标签、确认密码校验和密码要求提示。

**Architecture:** 保持 `AuthPage` 作为登录与初始化共用组件，只在 `initialize` 模式维护确认密码状态并执行前端一致性校验。后端初始化接口和认证上下文不变。

**Tech Stack:** React、TypeScript、React Testing Library、Vitest、Tailwind CSS。

## Global Constraints

- 标题仅在初始化页面居中显示。
- 初始化页面只增加“用户名”“密码”“确认密码”和“密码至少 8 个字符”的必要提示。
- 不增加管理员权限或产品介绍文案。
- 密码不一致时不得调用初始化接口。

---

### Task 1: 优化初始管理员表单

**Files:**
- Modify: `frontend/src/auth/auth-page.tsx`
- Modify: `frontend/src/auth/auth-flow.test.tsx`

**Interfaces:**
- Consumes: `useAuth().initialize(username: string, password: string): Promise<void>`
- Produces: 初始化模式确认密码校验；后端接口签名不变。

- [x] **Step 1: 写入失败测试**

在 `auth-flow.test.tsx` 中验证初始化页面的三个可见字段标签、密码要求提示，以及密码不一致时显示错误且只产生启动状态请求。

- [x] **Step 2: 运行测试并确认失败**

Run: `npm test -- --run src/auth/auth-flow.test.tsx`

Expected: 页面找不到新增标签或确认密码字段，测试失败。

- [x] **Step 3: 实现最小页面修改**

在 `AuthPage` 增加 `confirmPassword` 状态；初始化提交时比较两次密码。使用原生 `label` 与 `htmlFor` 关联输入框，显示密码要求提示，并仅给初始化标题增加居中样式。

- [x] **Step 4: 运行相关测试**

Run: `npm test -- --run src/auth/auth-flow.test.tsx`

Expected: 全部通过。

- [x] **Step 5: 执行完整前端验证**

Run: `npm test -- --run`

Expected: 全部通过。

Run: `npm run build`

Expected: TypeScript 检查和 Vite 生产构建成功。

- [x] **Step 6: 提交实现**

```bash
git add frontend/src/auth/auth-page.tsx frontend/src/auth/auth-flow.test.tsx docs/superpowers/plans/2026-07-16-initial-admin-form.md
git commit -m "feat: improve initial admin form"
```
