# Requirements Document

## Introduction

HomeTrove 的 v1 必备检索能力「以图搜图」：从一张照片（或视频封面/场景帧）出发，召回库中语义相近的资产。v1 简化版利用现有 `embedding.jina_clip` 插件的向量管道与 `sqlite-vec` 近邻索引：取目标资产的 image/scene 嵌入向量，做最近邻搜索，返回按距离排序的相似资产列表。前端在资源详情页提供「找相似」入口，结果以 justified 网格展示。

## Glossary

- **Embedding**: `embeddings` 表中的一行，含 `asset_id`、`scope`（image/scene/caption）、`t_start`/`t_end`（场景时间窗）、JSON 向量。
- **sqlite-vec index**: `embedding_vec` 虚拟表，`rowid` 对应 `embeddings.id`，支持最近邻 `search(vec, k)`。
- **scope=image**: 整张图片/视频封面帧的向量；**scope=scene**: 视频场景关键帧的向量。
- **distance**: sqlite-vec 返回的距离（越小越相似）。

## Requirements

### Requirement 1: 后端相似资产查询

**User Story:** AS 用户，I want 从某个资产出发查询相似资产，so that 能按视觉相似度找到相关内容。

#### Acceptance Criteria

1. WHEN 调用 `GET /api/assets/{asset_id}/similar`，THEN 系统 SHALL 返回按距离升序排列的相似资产列表。
2. WHEN 目标资产存在 image-scope 嵌入向量，THEN 系统 SHALL 以该向量执行最近邻搜索并返回 k 个最近邻。
3. WHEN 目标资产无 image 向量但存在 scene 向量，THEN 系统 SHALL 以最近的 scene 向量执行搜索。
4. WHEN 目标资产无任何嵌入向量，THEN 系统 SHALL 返回空列表（HTTP 200）。
5. WHEN 目标资产不存在或已软删除，THEN 系统 SHALL 返回 404。
6. WHEN 搜索返回近邻，THEN 系统 SHALL 排除目标资产自身、已软删除资产，且按距离升序返回。

### Requirement 2: 结果内容

**User Story:** AS 用户，I want 相似结果包含资产元数据与距离，so that 前端能渲染网格并标注相似度。

#### Acceptance Criteria

1. WHEN 返回相似资产，THEN 系统 SHALL 包含 `asset_id`、`media_type`、`duration_sec`、`distance`、`scope`、`t_start`、`t_end`。
2. WHEN 命中场景向量（scene scope），THEN 系统 SHALL 携带该场景的 `t_start`/`t_end`。

### Requirement 3: 前端「找相似」入口

**User Story:** AS 用户，I want 在资源详情页一键找相似，so that 从当前照片出发浏览相关照片。

#### Acceptance Criteria

1. WHEN 资源详情页加载且资产非回收站状态，THEN 系统 SHALL 显示「找相似」按钮。
2. WHEN 用户点击「找相似」，THEN 系统 SHALL 在详情页下方展示相似资产网格。
3. WHEN 相似结果为空，THEN 系统 SHALL 显示「暂无相似资产（该资产尚未生成嵌入向量）」提示。

### Requirement 4: 相似结果网格交互

**User Story:** AS 用户，I want 相似结果与主库交互一致，so that 能点击查看与跳转。

#### Acceptance Criteria

1. WHEN 渲染相似资产缩略图，THEN 系统 SHALL 使用与时间轴一致的缩略图样式与视频占位标识。
2. WHEN 用户点击相似资产，THEN 系统 SHALL 跳转到对应资源详情页。
3. WHEN 相似资产为视频场景命中，THEN 系统 SHALL 在网格上显示场景时间范围。
