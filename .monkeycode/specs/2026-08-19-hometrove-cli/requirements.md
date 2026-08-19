# Requirements Document: homeTrove-cli

## Introduction

homeTrove-cli 是一个面向 Agent 的命令行客户端，它把 HomeTrove 后端的常用能力通过 HTTP API 暴露为可脚本化的 CLI 命令。Agent 可以借助它上传文件、搜索素材库、测试新插件，而无需直接操作文件系统或数据库。

## Glossary

- **CLI**：homeTrove-cli 命令行工具，运行在 Agent 侧。
- **API Host**：HomeTrove 后端服务地址，默认通过环境变量配置。
- **API Key**：用于 CLI 与后端通信的固定密钥。
- **Endpoint Map**：CLI 内部维护的命令到 HTTP 方法、路径、参数的映射表。

## Requirements

### Requirement 1: CLI 作为 HTTP 转发器

**User Story:** 作为 Agent，我希望通过一个命令行工具调用 HomeTrove 后端接口，以便把现有功能脚本化。

#### Acceptance Criteria

1. The CLI SHALL read the API host from the environment variable `HOMETROVE_CLI_HOST`.
2. The CLI SHALL read the API key from the environment variable `HOMETROVE_CLI_API_KEY`.
3. WHEN the CLI invokes any endpoint, the CLI SHALL send the API key in an `Authorization` header.
4. IF the API key is missing or invalid, the backend SHALL reject the request with HTTP 401.

### Requirement 2: 动态端点映射与详情展示

**User Story:** 作为 Agent，我希望查看 CLI 支持哪些命令以及每个命令需要什么参数，以便正确调用。

#### Acceptance Criteria

1. The CLI SHALL maintain an internal endpoint map that binds command names to HTTP methods, paths, and parameter schemas.
2. WHEN the Agent runs `hometrove-cli endpoints`, the CLI SHALL list all available command names and their HTTP methods.
3. WHEN the Agent runs `hometrove-cli describe <command>`, the CLI SHALL print the parameter names, types, and descriptions for that command.
4. WHEN the Agent runs `hometrove-cli call <command> --key value ...`, the CLI SHALL build the request from the endpoint map and print the JSON response.

### Requirement 3: 文件上传

**User Story:** 作为 Agent，我希望把本地文件上传到 HomeTrove 并指定要运行的插件，以便自动建立索引。

#### Acceptance Criteria

1. WHEN the Agent runs `hometrove-cli upload <file>`, the CLI SHALL upload the file through the existing chunked upload API and ingest it with globally enabled plugins.
2. WHEN the Agent provides `--plugins p1,p2`, the CLI SHALL ingest the uploaded file using only the listed plugins.
3. WHEN the upload completes, the CLI SHALL print the new asset id and a markdown image URL pointing to `/api/assets/{asset_id}/file`.

### Requirement 4: 素材搜索

**User Story:** 作为 Agent，我希望按语义、分类、标签、人员等条件搜索素材库，并直接拿到 URL，以便在文章中插入图片。

#### Acceptance Criteria

1. WHEN the Agent runs `hometrove-cli search <query> --mode semantic`, the CLI SHALL call `/api/search` and return up to `--limit` results.
2. WHEN the Agent runs `hometrove-cli search --tag <tag> --category <category> --person-id <id>`, the CLI SHALL call `/api/assets` with the corresponding filters.
3. FOR every returned asset, the CLI SHALL output a markdown image tag using `/api/assets/{asset_id}/thumbnail` or `/api/assets/{asset_id}/file` as the URL.
4. The CLI SHALL support `--limit` to control the maximum number of returned URLs.

### Requirement 5: 插件测试

**User Story:** 作为 Agent，我希望在本地文件上直接运行一个插件并看到原始 JSON 输出，以便验证新插件是否工作。

#### Acceptance Criteria

1. WHEN the Agent runs `hometrove-cli test-plugin <plugin_id> <file>`, the CLI SHALL send the file to the backend plugin test endpoint.
2. The backend SHALL run the requested plugin against the file synchronously and return the raw JSON result.
3. IF the plugin does not exist or does not support the file media type, the backend SHALL return HTTP 400 with a clear error message.
4. The CLI SHALL print the returned JSON without transformation.
