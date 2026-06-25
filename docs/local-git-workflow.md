# 开发工作流

本文档记录开发与协作时的基本提交纪律。公开仓库协作时建议通过分支和 Pull Request 合并；个人本地开发也应保持小步提交，方便回溯和审查。

## 提交纪律

- 每完成一个可独立验证的小任务，提交一次 commit。
- 提交前优先执行相关测试；高风险改动再跑更大范围测试。
- 涉及代码风格、导入、格式或静态问题时执行 `uv run ruff check`。
- 涉及 Dockerfile、Compose、挂载路径、权限或运行时依赖时，再执行 Docker Compose 验证。
- 不要把多个不相关任务混在同一个 commit。
- 不要提交 `.env`、`.venv/`、`data/`、pytest/ruff 缓存或媒体文件。

## 分支与 Pull Request

- 主分支保持可运行。
- 功能、修复、文档调整建议使用短分支，例如 `fix/watch-stability` 或 `docs/docker-readme`。
- Pull Request 描述应包含变更摘要、验证命令和已知风险。
- 涉及文件删除、数据库迁移、媒体库写入或 Docker 部署行为时，在 PR 中明确影响面。

## 建议提交粒度

- 一个模型或仓储变更 + 对应测试。
- 一个 provider 方法 + 对应测试。
- 一个 UI 页面或交互 + 对应测试。
- 一个文件操作能力 + 回滚测试。
- 一个文档或配置调整。

## 推荐命令

```bash
uv run pytest
uv run ruff check
git status --short
git add <相关文件>
git commit -m "简短描述本次小任务"
```

## 回退原则

- 优先使用 `git diff` 和 `git status` 判断影响范围。
- 只回退当前任务相关文件。
- 不要使用 `git reset --hard`，除非用户明确要求。
