# homeTrove-cli Design

Feature Name: 2026-08-19-hometrove-cli
Updated: 2026-08-19

## Description

homeTrove-cli 是一个轻量级 Agent 侧命令行工具，它把 HomeTrove 后端 REST API 封装成可脚本化的子命令。CLI 自身只做 HTTP 转发和参数拼接，核心逻辑仍在后端。这样 Agent 可以在不接触文件系统的前提下完成上传、搜索和插件测试。

## Architecture

```text
+-------------+      HTTP + API Key       +------------------+
| Agent shell |  <-----------------------> | HomeTrove API    |
| hometrove-cli|     Authorization: Bearer | (FastAPI)        |
+-------------+                            +------------------+
     |                                            |
     | env: HOMETROVE_CLI_HOST                    | /api/uploads
     | env: HOMETROVE_CLI_API_KEY                 | /api/search
     |                                            | /api/assets
     |                                            | /api/plugins/{id}/test
     v                                            v
  Endpoint Map (Python dict)               TokenAuthBackend
```

## Components and Interfaces

### 1. CLI Client (`hometrove/cli_agent.py`)

- `AgentCLI` 类：负责读取环境变量、构建 HTTP 请求、处理响应、格式化输出。
- `ENDPOINTS` 字典：声明式地定义每个子命令对应的 method、path、params、help。
- 子命令函数：`cmd_endpoints`、`cmd_describe`、`cmd_call`、`cmd_upload`、`cmd_search`、`cmd_test_plugin`。

### 2. 认证后端 (`hometrove/auth.py`)

- `TokenAuthBackend`：检查请求头 `Authorization: Bearer <token>`，与 `settings.cli_api_key` 比对。匹配时返回 `Principal(id="cli", is_authenticated=True, scopes={"cli"})`。
- `build_auth_backend` 在 `auth_backend=token` 或显式配置 token 时启用该后端。

### 3. 插件测试端点 (`hometrove/api/routes/plugins.py`)

- `POST /api/plugins/{plugin_id}/test`：接收 multipart 文件，保存到临时目录，构造 `AssetLike` 与 `PluginContext`，同步调用 `plugin.run()`，返回原始 JSON。
- 依赖 `require_scope("cli")` 或 `require_auth`，确保只有认证过的 CLI 调用。

### 4. 配置 (`hometrove/config.py`)

- 新增 `cli_api_key: str | None = None`，通过 `HOMETROVE_CLI_API_KEY` 注入。
- 新增 `auth_backend` 已存在；当配置了 `cli_api_key` 时后端自动切换到 token 认证。

## Data Models

### Endpoint Map Entry

```python
{
    "search": {
        "method": "GET",
        "path": "/api/search",
        "params": {
            "q": {"type": "str", "required": True, "help": "search query"},
            "limit": {"type": "int", "default": 10, "help": "max results"},
        },
        "help": "semantic hybrid search",
    }
}
```

### CLI 返回格式

- `endpoints`：命令名列表（纯文本）。
- `describe`：参数表格（Markdown）。
- `call`：后端 JSON 原样输出（pretty-print）。
- `upload`：`{asset_id, url}` JSON，附带 markdown 图片标签。
- `search`：每行一个 markdown 图片 URL。
- `test-plugin`：插件原始 JSON 输出。

## Correctness Properties

- CLI 不存储密钥到磁盘，只从环境变量读取。
- 上传走现有分片 API，复用断点续传和幂等校验。
- 插件测试端点不写入 `plugin_results` 或 `assets`，是纯只读/临时运行。
- 所有 CLI 路由都要求认证，避免未授权访问。

## Error Handling

- 环境变量缺失：CLI 启动时打印错误并退出码 2。
- 后端返回 4xx/5xx：CLI 打印响应体并退出码 1。
- 插件不支持媒体类型：返回 400，提示支持的类型。
- 网络不可达：打印异常并退出码 1。

## Test Strategy

- 单元测试使用 `fastapi.testclient.TestClient` 启动带 token 认证的后端。
- 测试覆盖：认证失败、端点列表、describe 输出、upload 流程、search 输出格式、test-plugin 端点。
- CLI 进程测试通过 `subprocess` 调用真实脚本并校验输出。
