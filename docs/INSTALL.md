# HomeTrove 安装与首次运行（M0）

M0 是「扫描 → 入库 → 浏览」的最小闭环。本文面向首次部署：如何挂载只读媒体目录、启动服务、执行第一次扫描，并在 Web 端看到时间轴。

**HomeTrove 只需要 Python 环境即可运行**（无需 Docker、无需 Node）。前端构建产物已随仓库附带，由 API Server 直接托管。

## 1. 前置条件

- Python 3.12+
- 一个包含照片 / 视频的目录（下称 `MEDIA_DIR`）
- 系统依赖（OpenCV / PyAV 运行时库）：

```bash
# Debian / Ubuntu
sudo apt-get update
sudo apt-get install -y libxcb1 libxcb-shape0 libxcb-xfixes0 libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 libgomp1
```

包名已整理在 `requirements-system.txt`，可直接：

```bash
sudo apt-get install -y $(grep -v '^#' requirements-system.txt | xargs)
```

> 若跳过系统依赖，`basic.scene_detect` 等依赖 `cv2` 的插件会返回 `skipped: scenedetect not installed`，测试 `test_scene_detect_finds_cuts` 也会失败。

## 2. 安装 Python 包

```bash
# 方式 A：源码安装（推荐开发）
pip install -e .

# 方式 B：生产（无 -e）
pip install .
```

依赖自动安装：FastAPI、uvicorn、SQLAlchemy 2.0、pydantic-settings、python-multipart、alembic、httpx、PyAV、PySceneDetect、scenedetect、Pillow、numpy、InsightFace（仅 `face.detect` 首次运行时会自动下载 `buffalo_l` 模型权重）。

### 可选依赖

| 插件 | 可选包 | 不装时表现 | 何时需要 |
|---|---|---|---|
| `face.detect` | InsightFace + onnxruntime（默认随主包安装） | 返回 `skipped: insightface unavailable`（insightface 不可用） | 需要真实人脸检测 / 命名时 |
| `asr.faster_whisper` | `pip install faster-whisper` | 返回 `skipped: faster_whisper unavailable`（使用 mock 字幕保持数据通路可观察） | 需要真实语音转写时 |

> **`asr.faster_whisper` 默认 mock 路径**：v1 不强制要求 `faster-whisper`。即便不装包，插件仍会向 `asr_transcripts` 表写入 2–4 段确定性字幕，详情页和 `/api/search` 关键词召回照样可用。装上 `faster-whisper` 后下一次重跑会自动切到真模型（首次会下载 small 模型权重，约 460 MB）。

## 3. 配置环境变量

| 变量 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `HOMETROVE_MEDIA_ROOTS` | 是 | 空 | 媒体根目录，多个用 `\|` 分隔（如 `/photos\|/videos`） |
| `HOMETROVE_DATA_DIR` | 否 | `./var` | 数据库与上传暂存目录（SQLite 文件 `hometrove.db` 在此） |
| `HOMETROVE_READ_ONLY_CHECK` | 否 | `warn` | M0-3 媒体根只读校验级别：`warn`（默认，writable 根打印 WARNING + 提示）+ `off`（跳过探测）。生产建议保持只读挂载（`mount -o ro`），dev / 临时 scratch 目录可设为 `off` 关闭 |
| `HOMETROVE_TRASH_RETENTION_DAYS` | 否 | `30` | v1 回收站软删除资产保留天数；`<=0` 关闭自动清理。配合 `HOMETROVE_TRASH_AUTO_PURGE=true` 使用 |
| `HOMETROVE_TRASH_AUTO_PURGE` | 否 | `false` | 启用后 worker 每 ~30s 自动调用 `purge_expired()`。生产建议 `true` |
| `HOMETROVE_LOG_LEVEL` | 否 | `INFO` | 日志级别 |

示例：

```bash
export HOMETROVE_MEDIA_ROOTS=/mnt/photos
export HOMETROVE_DATA_DIR=/var/lib/hometrove
```

> **只读挂载**：M0 约定原始媒体目录只读。程序不会向媒体根目录写任何内容；上传的文件统一进入 `HOMETROVE_DATA_DIR/staging/`。`hometrove` 启动时会为每个 `HOMETROVE_MEDIA_ROOTS` 路径写一个 1 字节 sentinel 后立即删除（探测用），若该路径可写则日志会输出 `WARNING: media root is WRITABLE — HomeTrove can modify originals: <path>`，并提示「set HOMETROVE_READ_ONLY_CHECK=off to silence, or remount the root read-only (mount -o ro)」。生产部署务必保持原始媒体目录只读挂载。

## 4. 启动服务（推荐：单命令）

`hometrove serve` 在一个 Python 进程里同时运行 API Server（对外 HTTP，端口 8080）与 Worker（后台执行插件）：

```bash
hometrove serve
```

首次启动会自动创建数据库 schema（幂等）。Alembic 迁移作为生产升级路径：`hometrove migrate`。

前端由 API Server 直接托管（`web/dist` 构建产物）——浏览器打开 `http://127.0.0.1:8080` 即可使用，无需单独的 Web 服务。`web/dist` 已随仓库提交，克隆后即可直接运行，无需 Node 环境。

### 4.1 Windows 用户（`scripts/init.bat` + `scripts/start.bat`）

仓库提供两个 Windows 批处理脚本，封装上面的 CLI 步骤：

- `scripts\init.bat` — 首次运行前执行：跑迁移 + 设置 vault 主密码（交互式或读 `HOMETROVE_VAULT_PASSWORD` 环境变量）。幂等，升级后也可以再跑一次。
- `scripts\start.bat` — 每次启动服务（API + worker 同进程，即 `hometrove serve`）。

```cmd
:: 1. 首次运行：迁移 + 设置 vault 密码
scripts\init.bat

:: 2. 启动服务
scripts\start.bat
```

两个脚本都会默认把数据目录放在仓库下的 `var\`，可通过设置 `HOMETROVE_DATA_DIR` / `HOMETROVE_MEDIA_ROOTS` 等环境变量覆盖。`init.bat` 的 vault 步骤可选跳过（输 `S`），事后通过 `POST /api/vault/setup` 补做。

## 5. 执行第一次扫描

启动后，媒体目录中的文件不会自动入库，需要手动触发一次扫描：

```bash
hometrove scan
# 输出示例：new=24 skipped=0 enqueued=24
```

Worker 随即消费 `jobs` 队列，对每个新资产运行 `basic.info` 插件（读取文件名、媒体类型、大小、mtime，以及标准库可解析的图片宽高）。可用以下接口查看进度：

```bash
curl http://127.0.0.1:8080/api/jobs      # 队列与进度统计
curl http://127.0.0.1:8080/api/health   # 健康检查
```

## 6. 浏览器访问

- 时间轴：`http://127.0.0.1:8080/timeline`（图片真实缩略图，点击放大或查看详情；视频在线播放；响应式网格）
- 文件详情：`http://127.0.0.1:8080/asset/1`（展示该文件所有插件的分析结果，字段为动态渲染）
- 文件夹：`http://127.0.0.1:8080/folders`（按原始目录树浏览）
- 标签 / 分类 / 人脸：`/tags`、`/categories`、`/faces`（模拟插件 mock.tags / mock.category / mock.faces 生成示例数据，点击标签筛选文件；真实插件后续替换）
- 任务中心：`http://127.0.0.1:8080/jobs`（以文件为单位聚合，最新在最上，支持失败重试）
- 上传：`http://127.0.0.1:8080/upload`

页面为响应式：手机端顶部导航栏 + 内容全宽，桌面端左侧栏布局。

## 7. 分片上传（M0 附加功能）

`/upload` 页支持大文件分片上传与断点续传。流程：

1. `POST /api/uploads` 创建会话（返回 `upload_id` 与 `chunk_size`）；
2. `PUT /api/uploads/{upload_id}/chunks/{idx}` 上传分片（幂等，可重传）；
3. `GET /api/uploads/{upload_id}` 查询已传分片（断点续传依赖它）；
4. `POST /api/uploads/{upload_id}/complete` 合并并校验 sha256；
5. `POST /api/uploads/{upload_id}/ingest` 入库，自动加入索引队列。

## 8. 进程拆分运行（可选）

单进程 `serve` 适合本地与小规模部署。若要拆分 API 与 Worker 为独立进程（如分别扩容、独立排查）：

```bash
# 终端 1：API Server（端口 8080）
hometrove api

# 终端 2：Worker
hometrove worker
```

## 9. 回收站维护（可选）

`POST /api/assets/{id}/trash` 软删除资产，`/trash` 页可一键还原或永久清空。批量场景可直接用 `POST /api/bulk/assets/trash` / `restore`（body `{"asset_ids": [...]}`，上限 1000，返回 `{requested, affected, missing}`）。

共享相册链接通过 `POST /api/albums/{album_id}/shares` 生成，公开访问路径为 `/share/{token}`；`allow_original`/`allow_download` 控制原图访问，`expires_at` 设置过期时间（秒级 Unix 时间戳）。定时清理可走以下两条路径之一：

```bash
# 路径 A：worker 内自动清扫（每 ~30s 一次）
export HOMETROVE_TRASH_AUTO_PURGE=true
export HOMETROVE_TRASH_RETENTION_DAYS=30   # 默认值

# 路径 B：cron / systemd timer 一次性清理
hometrove trash prune [--older-than-days N] [--dry-run]
```

注意：**软删除只动数据库索引，不删磁盘上的源文件**——HomeTrove 尊重 M0 关于「媒体根只读」的不变量。还原操作把资产重新加入索引；如果用户在外部手动删了文件，索引会保留到下一次 `hometrove scan` 的去重检查才消失。

## 10. 修改前端（可选）

前端为 React + Vite 源码（`web/`）。修改后需要 Node 环境重新构建，产物由 API 托管：

```bash
cd web
npm install
npm run build   # 产出 web/dist，API Server 重启后生效
```

若不需要改前端，运行阶段完全不需要 Node。

## 11. Agent CLI（可选）

HomeTrove 提供一个面向自动化/LLM Agent 的命令行客户端 `hometrove-cli`，它通过 HTTP 转发调用后端 API，适合远程脚本或 Agent 工具链集成。

启用服务端 token 认证：

```bash
export HOMETROVE_CLI_API_KEY=changeme
hometrove serve
```

客户端使用：

```bash
export HOMETROVE_CLI_HOST=http://127.0.0.1:8080
export HOMETROVE_CLI_API_KEY=changeme

# 查看可用命令
hometrove-cli endpoints
hometrove-cli describe search

# 上传（默认加密写入 vault）
hometrove-cli upload ./photo.jpg --plugins basic.info,thumbnail

# 搜索
hometrove-cli search "sunset beach" --limit 5

# 插件快速测试
hometrove-cli test-plugin basic.info ./photo.jpg

# vault 管理
hometrove-cli vault setup --password <pwd> --confirm <pwd>
hometrove-cli vault unlock --password <pwd>
```

> 若服务端 vault 已默认启用，上传前需先执行 `vault setup` / `unlock`，否则加密上传会返回 423。

## 12. 常见问题

- **`hometrove: command not found`**：确认已 `pip install -e .`，且该命令在你的 PATH 中（通常 `~/.local/bin`）。
- **扫描后 `jobs` 一直是 pending**：确认服务进程在运行（`hometrove serve` 或 `hometrove worker`）。
- **`GET /api/assets` 返回空**：确认 `HOMETROVE_MEDIA_ROOTS` 已设置且目录可读，然后重新执行 `hometrove scan`。
- **浏览器打开 404**：确认启动目录是仓库根目录（`web/dist` 需能被定位）。
