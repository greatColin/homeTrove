# HomeTrove 安装与首次运行（M0）

M0 是「扫描 → 入库 → 浏览」的最小闭环。本文面向首次部署：如何挂载只读媒体目录、启动服务、执行第一次扫描，并在 Web 端看到时间轴。

**HomeTrove 只需要 Python 环境即可运行**（无需 Docker、无需 Node）。前端构建产物已随仓库附带，由 API Server 直接托管。

## 1. 前置条件

- Python 3.11+
- 一个包含照片 / 视频的目录（下称 `MEDIA_DIR`）

后端无额外系统依赖（M0 的 `basic.info` 插件零外部库，仅用标准库解析图片宽高）。

## 2. 安装 Python 包

```bash
# 方式 A：源码安装（推荐开发）
pip install -e .

# 方式 B：生产（无 -e）
pip install .
```

依赖自动安装：FastAPI、uvicorn、SQLAlchemy 2.0、pydantic-settings、python-multipart、alembic、httpx。

## 3. 配置环境变量

| 变量 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `HOMETROVE_MEDIA_ROOTS` | 是 | 空 | 媒体根目录，多个用 `\|` 分隔（如 `/photos\|/videos`） |
| `HOMETROVE_DATA_DIR` | 否 | `./var` | 数据库与上传暂存目录（SQLite 文件 `hometrove.db` 在此） |
| `HOMETROVE_LOG_LEVEL` | 否 | `INFO` | 日志级别 |

示例：

```bash
export HOMETROVE_MEDIA_ROOTS=/mnt/photos
export HOMETROVE_DATA_DIR=/var/lib/hometrove
```

> **只读挂载**：M0 约定原始媒体目录只读。程序不会向媒体根目录写任何内容；上传的文件统一进入 `HOMETROVE_DATA_DIR/staging/`。

## 4. 启动服务（推荐：单命令）

`hometrove serve` 在一个 Python 进程里同时运行 API Server（对外 HTTP，端口 8080）与 Worker（后台执行插件）：

```bash
hometrove serve
```

首次启动会自动创建数据库 schema（幂等）。Alembic 迁移作为生产升级路径：`hometrove migrate`。

前端由 API Server 直接托管（`web/dist` 构建产物）——浏览器打开 `http://127.0.0.1:8080` 即可使用，无需单独的 Web 服务。`web/dist` 已随仓库提交，克隆后即可直接运行，无需 Node 环境。

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

## 9. 修改前端（可选）

前端为 React + Vite 源码（`web/`）。修改后需要 Node 环境重新构建，产物由 API 托管：

```bash
cd web
npm install
npm run build   # 产出 web/dist，API Server 重启后生效
```

若不需要改前端，运行阶段完全不需要 Node。

## 10. 常见问题

- **`hometrove: command not found`**：确认已 `pip install -e .`，且该命令在你的 PATH 中（通常 `~/.local/bin`）。
- **扫描后 `jobs` 一直是 pending**：确认服务进程在运行（`hometrove serve` 或 `hometrove worker`）。
- **`GET /api/assets` 返回空**：确认 `HOMETROVE_MEDIA_ROOTS` 已设置且目录可读，然后重新执行 `hometrove scan`。
- **浏览器打开 404**：确认启动目录是仓库根目录（`web/dist` 需能被定位）。
