# 部署与排障

本文档记录 Media Pilot 的 Docker Compose、Prowlarr / qBittorrent 初始化和常见部署排障。仓库根目录的 `docker-compose.yml` 面向发布镜像部署；源码本地构建使用 `docker-compose.dev.yml` 作为 override。

## Docker Compose

启动：

```bash
docker compose up -d
```

`MEDIA_PILOT_IMAGE` 决定主应用、初始化容器和 Prowlarr indexer 初始化容器使用的镜像。正式部署时填写发布到 Docker Hub 的镜像名；本地源码构建时使用：

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml build media-pilot
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

启动流程：

1. `media-pilot-postgres` 启动并通过 healthcheck。
2. `media-pilot-init` 生成 Prowlarr API Key 与 qBittorrent WebUI 密码，写入 `/data/shared-secrets.env`，并同步到 Prowlarr / qB 配置文件。
3. `media-pilot-prowlarr-init` 等 Prowlarr API 可用后幂等创建默认公共 indexer。
4. `media-pilot` 等 PostgreSQL 和两个 one-shot init 服务就绪后启动。

反复 `docker compose down && docker compose up -d` 时，凭据与已创建 indexer 会保留；用户在 Prowlarr UI 中调整过的 indexer 不会被覆盖。

Compose 关键约束：

- 下载、watch、工作区、媒体库、数据库目录均需要可写；源文件清理策略会移动 watch/downloads 下的任务输入节点
- `.env` 中的目录变量表示宿主机挂载路径；容器内应用路径固定为 `/data/...`
- 源文件回收区挂载到容器内 `/data/trash`，建议与下载、watch、媒体库目录隔离
- PostgreSQL 是默认生产数据库；`MEDIA_PILOT_DATABASE_URL` 默认连接 compose 内的 `media-pilot-postgres`
- LLM / TMDB / TPDB API Key 通过环境变量注入
- Prowlarr API Key 与 qBittorrent WebUI 密码由 `media-pilot-init` 接管

## 数据库

默认 compose 使用 PostgreSQL。相关变量：

```dotenv
POSTGRES_DATA_DIR=./data/postgres
POSTGRES_DB=media_pilot
POSTGRES_USER=media_pilot
POSTGRES_PASSWORD=change-this-postgres-password
MEDIA_PILOT_DATABASE_URL=postgresql+psycopg://media_pilot:change-this-postgres-password@media-pilot-postgres:5432/media_pilot
```

`MEDIA_PILOT_DATABASE_DIR` 仍保留给 SQLite 回退和旧库迁移使用。未设置 `MEDIA_PILOT_DATABASE_URL` 时，应用会回退到 `MEDIA_PILOT_DATABASE_DIR/media-pilot.sqlite3`。

### 从 SQLite 迁移到 PostgreSQL

迁移前先停止应用并备份旧库：

```bash
docker compose stop media-pilot
cp ./data/db/media-pilot.sqlite3 ./data/db/media-pilot.sqlite3.bak
```

启动 PostgreSQL：

```bash
docker compose up -d media-pilot-postgres
```

执行迁移：

```bash
docker compose run --rm media-pilot \
  sh -lc 'python -m media_pilot.deployment.migrate_sqlite_to_postgres \
  --sqlite-path /data/db/media-pilot.sqlite3 \
  --database-url "$MEDIA_PILOT_DATABASE_URL" \
  --clean-stale-active-runs'
```

迁移脚本只读取 SQLite 文件，不会修改旧库。`--clean-stale-active-runs` 会把旧库里因 SQLite 锁残留的 `active` / `agent_running` 迁为失败态，避免新库继续显示卡住。

## Prowlarr Indexer

`media-pilot-prowlarr-init` 默认创建公共 indexer 集合：

- YTS
- The Pirate Bay
- LimeTorrents
- Nyaa.si
- Mikan
- dmhy
- ACG.RIP

默认集合不包含成人源或私有 / 邀请站。启动后可自行在 Prowlarr UI 中增删改 indexer，也可调整 FlareSolverr。

## 手动凭据接管

非 Docker 部署或需要排障接管凭据时，可以在 `.env` 中显式填：

```dotenv
MEDIA_PILOT_PROWLARR_API_KEY=
MEDIA_PILOT_QBITTORRENT_PASSWORD=
```

显式 env 优先于共享 secrets，不会触发新的随机生成。Docker 场景下若 `media-pilot-init` 启动失败，可用此方式临时接管并重启 `media-pilot`。

## qBittorrent 5.x 旧密码摘要修复

早期版本可能把 qB WebUI 密码写成 PBKDF2-HMAC-SHA1 + 10000 iterations。qBittorrent 5.x / linuxserver 新镜像使用 PBKDF2-HMAC-SHA512 + 100000 iterations，旧 SHA-1 / 10000 摘要会导致 WebUI 登录返回 `401 Unauthorized`，即使共享 secrets 里的明文密码是正确的。

当前代码故意不把旧 hash 判为有效，避免出现“主应用校验通过，但 qB WebUI 实际拒绝登录”的静默失败。修复必须用 `media-pilot-init` force 覆盖 qB 配置。

步骤：

1. 停止 qB 容器，避免覆盖过程中读到半截状态：

   ```bash
   docker compose stop media-pilot-qbittorrent
   ```

2. 强制运行 init 容器覆盖摘要。推荐方式：

   ```bash
   docker compose run --rm \
     -e MEDIA_PILOT_BOOTSTRAP_FORCE=1 \
     media-pilot-init
   ```

   也可以使用 compose 已透传环境变量的方式：

   ```bash
   MEDIA_PILOT_BOOTSTRAP_FORCE=1 \
     docker compose up -d --force-recreate media-pilot-init
   ```

3. 重启 qB 容器：

   ```bash
   docker compose restart media-pilot-qbittorrent
   ```

4. 重启主应用或刷新设置页连接状态：

   ```bash
   docker compose restart media-pilot
   ```

完成后使用共享 secrets 中记录的同一份明文密码登录 qB WebUI。不要复制 `qBittorrent.conf` 里的 hash 字段，那是摘要不是密码。

## 高级环境变量

`.env.example` 主体只保留首次部署必填或常用配置。以下高级变量仍被运行时代码支持：

- 非 Docker 运行时，直接作为进程环境变量注入。
- 使用仓库内置 compose 时，只有 `docker-compose.yml` 中显式声明的变量会被传入容器；如果要覆盖未声明的高级变量，需要在 `media-pilot.environment` 中增加对应条目，或维护自己的生产 compose。

| 变量 | 默认 | 用途 |
| --- | --- | --- |
| `MEDIA_PILOT_LLM_TIMEOUT_SECONDS` | `30` | LLM 调用超时秒数 |
| `MEDIA_PILOT_LLM_PROMPT_PROFILE` | `tmdb_movie` | LLM Prompt 档案 |
| `MEDIA_PILOT_LLM_REPLY_LANGUAGE` | `auto` | Agent 对话回复语言 |
| `MEDIA_PILOT_TMDB_BASE_URL` | `https://api.themoviedb.org/3` | TMDB API 基础 URL |
| `MEDIA_PILOT_TMDB_LANGUAGE_PRIORITY` | `zh-CN,en-US` | 元数据语言优先级 |
| `MEDIA_PILOT_TMDB_TIMEOUT_SECONDS` | `10` | TMDB 调用超时秒数 |
| `MEDIA_PILOT_TMDB_IMAGE_BASE_URL` | `https://image.tmdb.org/t/p` | TMDB 图片基础 URL |
| `MEDIA_PILOT_TMDB_POSTER_SIZE` | `w780` | 海报尺寸 |
| `MEDIA_PILOT_TMDB_BACKDROP_SIZE` | `w1280` | 背景图尺寸 |
| `MEDIA_PILOT_TMDB_LOGO_SIZE` | `w500` | Logo 尺寸 |
| `MEDIA_PILOT_TMDB_PROFILE_SIZE` | `w185` | 头像尺寸 |
| `MEDIA_PILOT_METADATA_AUTO_CONFIRM_CONFIDENCE` | `0.9` | 高置信度自动确认阈值 |
| `MEDIA_PILOT_METADATA_AUTO_CONFIRM_MARGIN` | `0.08` | 自动确认最低差距 |
| `MEDIA_PILOT_PROWLARR_URL` | `http://media-pilot-prowlarr:9696` | Prowlarr 服务 URL |
| `MEDIA_PILOT_PROWLARR_TIMEOUT_SECONDS` | `15` | Prowlarr 调用超时秒数 |
| `MEDIA_PILOT_QBITTORRENT_URL` | `http://media-pilot-qbittorrent:8080` | qB 服务 URL |
| `MEDIA_PILOT_QBITTORRENT_USERNAME` | `admin` | qB 用户名 |
| `MEDIA_PILOT_QBITTORRENT_SAVE_PATH` | `/data/downloads` | qB 保存路径 |
| `MEDIA_PILOT_QBITTORRENT_CATEGORY` | `media-pilot` | qB 分类 |
| `MEDIA_PILOT_QBITTORRENT_TIMEOUT_SECONDS` | `15` | qB 调用超时秒数 |
| `MEDIA_PILOT_TRASH_DIR` | - | 源文件移入回收区目标目录 |
| `PUID` / `PGID` | `1000` | linuxserver 容器进程用户 / 组 |
| `TZ` | `Asia/Shanghai` | 时区 |
