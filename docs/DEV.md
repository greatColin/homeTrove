# HomeTrove 开发指南（M0）

面向本地开发：如何跑测试、起前后端、理解代码结构、如何新增一个插件。

## 1. 代码结构

```
hometrove/              后端 Python 包
├── api/                 FastAPI 应用与路由
│   └── routes/          assets / folders / jobs / health
├── bootstrap.py         schema 初始化
├── cli.py               CLI 入口（api / worker / scan / migrate）
├── config.py            pydantic-settings 配置
├── db.py                SQLAlchemy 引擎 / session / WAL
├── events.py            进程内事件总线（SSE 用）
├── models.py            ORM：assets / plugin_results / jobs / plugin_config
├── orchestrator/        DAG 拓扑排序 + 插件执行器
│   ├── dag.py           构建依赖图（topological_sorter）
│   └── runner.py        执行单个 job，写 plugin_results
├── plugins/             插件框架
│   ├── base.py          插件基类
│   ├── api.py           插件 meta 描述
│   ├── registry.py      插件注册表
│   └── builtin/         内置插件（basic_info）
├── scanner/             扫描器（发现 / 去重 / 入库 / 入队）
│   └── ingest.py        上传文件入库
├── uploads/             分片上传 / 断点续传
└── worker/              常驻 worker 进程
web/                     前端（React + Vite + Tailwind）
├── src/routes/          timeline / folders / jobs / upload 页面
alembic/                 Alembic 迁移
tests/                   pytest 冒烟测试
```

## 2. 本地开发环境

```bash
pip install -e .[dev]
cd web && npm install
```

## 3. 跑后端测试

```bash
# 全部测试
python -m pytest tests/ -q

# 单个文件
python -m pytest tests/test_smoke.py -q
```

当前 `tests/test_smoke.py` 覆盖：扫描去重、basic.info 插件、DAG 排序、健康接口、分片上传往返。测试使用独立临时目录，不污染本地数据库。

## 4. 起前后端（开发模式）

**快速起步（单进程）**：

```bash
export HOMETROVE_MEDIA_ROOTS=/path/to/photos
export HOMETROVE_DATA_DIR=./var

# API + worker + 前端静态托管，一条命令
hometrove serve

# 另开终端，触发扫描
hometrove scan
```

浏览器打开 `http://localhost:8080`。

**拆分进程（可选）**：

```bash
export HOMETROVE_MEDIA_ROOTS=/path/to/photos
export HOMETROVE_DATA_DIR=./var

# 终端 1：API（端口 8080，托管 web/dist）
hometrove api

# 终端 2：worker
hometrove worker

# 终端 3：一次性扫描
hometrove scan
```

**前端热更新（改前端代码时）**：

```bash
cd web
npm install
npm run dev      # Vite dev server，端口 5173，/api 代理到 8080
npm run build    # 产出 web/dist，API Server 重启后生效
```

生产构建：`npm run build`（产物在 `web/dist/`，由 API Server 直接托管）。

## 5. 如何新增一个插件（M1 预告）

M0 只有 `basic.info`。新增插件分四步：

1. 在 `hometrove/plugins/builtin/` 新建模块，实现 `hometrove/plugins/base.py` 中的 `Plugin` 接口（`id` / `version` / `depends_on` / `run(asset, ctx)`）；
2. 在模块底部注册到注册表（`REGISTRY.register(...)`），确保模块在 `plugins/builtin/__init__.py` 中被 import；
3. 若插件依赖其他插件产出，在 `depends_on` 中声明——DAG 调度器会自动保证执行顺序；
4. 为存量资产补跑：调整 `enqueue_*` 逻辑或手动写一条 pending job，worker 会自动消费。

## 6. 数据库

- 默认 SQLite（WAL 模式）位于 `HOMETROVE_DATA_DIR/hometrove.db`；
- 首次连接自动建表（幂等），Alembic 迁移为生产升级路径：`hometrove migrate`；
- `assets.path` 用 NUL（`\0`）分隔 `media_root` 与相对路径——Linux 路径不允许 NUL，可避免分隔符与真实文件名冲突。

## 7. 已知约定

- 资产路径存储：`{media_root}\0{relative_path}`（上传资产为 `uploads\0{absolute_staging_path}`）；
- 图片宽高解析零外部依赖，仅标准库（PNG / GIF / BMP / JPEG / WEBP）；JPEG 需要标准 SOF 段，无法解析时跳过不报错；
- worker 与 API 分离为两个模块，可用 `hometrove serve` 同进程运行（worker 跑在后台线程），也可用 `hometrove api` + `hometrove worker` 拆分为两个进程；共享同一 SQLite 文件。事件总线为进程内 pub/sub，SSE 仅作用于 API 进程。
- `run_forever()` 仅在主线程注册信号处理器；在 worker 线程中运行时自动跳过（否则 `add_signal_handler` 会抛 `RuntimeError`）。
