# 技术设计：插件重跑范围选择

## 接口变更

### 新增 GET `/api/plugins/{plugin_id}/rerun-candidates`

查询可重跑的候选资产。

Query 参数：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| q | string | 否 | 文件名/路径模糊搜索 |
| media_type | string | 否 | image / video / other；不传则按插件 supported_media 过滤 |
| limit | int | 否 | 默认 50，最大 200 |
| offset | int | 否 | 默认 0 |

返回：

```json
{
  "items": [
    {"asset_id": 1, "filename": "xxx.jpg", "path": "/photos/xxx.jpg", "media_type": "image"}
  ],
  "total": 100
}
```

### 新增 POST `/api/plugins/{plugin_id}/rerun-selected`

对选中资产重跑指定插件。

请求体：

```json
{"asset_ids": [1, 2, 3]}
```

返回：

```json
{"dropped": 5, "enqueued": 3}
```

逻辑复用现有的 `rerun_plugin`，但改为按 `asset_ids` 过滤。

## 后端实现

### 路由

在 `hometrove/api/routes/plugins.py` 新增两个端点。

### 候选查询逻辑

```python
from hometrove import db
from hometrove.models import Asset

async def get_rerun_candidates(
    plugin_id: str,
    supported_media: list[str],
    q: str | None,
    media_type: str | None,
    limit: int,
    offset: int,
):
    conds = [Asset.media_type.in_(supported_media)]
    if media_type:
        conds.append(Asset.media_type == media_type)
    if q:
        pattern = f"%{q}%"
        conds.append((Asset.filename.like(pattern)) | (Asset.path.like(pattern)))
    total = await db.count(Asset, *conds)
    rows = await db.fetch(
        Asset,
        *conds,
        order_by=[Asset.id.desc()],
        limit=limit,
        offset=offset,
    )
    return {"items": [...], "total": total}
```

### 重跑逻辑

复用 `hometrove/jobs.py` 或 `rerun_plugin` 中的任务删除与入队逻辑，只处理给定 `asset_ids`。

## 前端实现

### 组件拆分

在 `web/src/routes/plugins.tsx` 中新增 `RerunScopeModal`：

- props: `plugin: PluginDTO`, `onClose: () => void`
- state:
  - `mode: "all" | "selected"`，默认 `selected`
  - `q: string`，防抖 200ms
  - `candidates: Candidate[]`
  - `selected: Set<number>`
  - `loading`, `submitting`

### 弹窗布局

- 桌面：居中 `max-w-2xl`，高度 `max-h-[80vh]`
- 移动端：全屏或接近全屏
- 顶部：模式切换（「全部文件」/「选择文件」）、搜索框
- 中部：候选列表（虚拟滚动或分页），每项可勾选
- 底部：已选数量、取消、确认重跑

### 模式说明

- 「全部文件」：按插件 `supported_media` 过滤全库资产，不加载列表，仅显示数量。
- 「选择文件」：加载候选列表，支持搜索和多选。

### API 调用

- 候选：`GET /api/plugins/{plugin.id}/rerun-candidates?q=...&limit=50&offset=0`
- 重跑：`POST /api/plugins/{plugin.id}/rerun-selected {asset_ids: [...]}`

### 确认后

- 关闭弹窗
- 提示入队数量
- `invalidateQueries(["jobs"])`

## 兼容性

- 旧的整库重跑接口保留，弹窗内「全部文件」模式调用新的 `/rerun-selected`，不直接调用旧接口。
- 若插件 `supported_media` 为空，则不过滤媒体类型。

## 测试

- 后端：
  - 候选接口按媒体类型过滤正确
  - 搜索模糊匹配文件名/路径
  - 选中重跑仅影响指定资产
- 前端：
  - 弹窗打开/关闭正常
  - 搜索防抖生效
  - 全选/取消全选行为正确
