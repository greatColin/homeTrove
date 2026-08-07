# Requirements Document

## Introduction

HomeTrove 的 v1 必备检索能力「高级筛选」：在时间轴页提供可展开的组合筛选面板，支持媒体类型、日期范围、人物、地点、标签、收藏状态等条件叠加过滤，结果以现有 justified 网格展示并支持无限滚动。后端 `/api/assets` 已支持 `media_type`、`favorite`、`tag`、`category`、`person_id`，本需求补充 `taken_after`/`taken_before`（日期范围）与 `place`（地点）过滤，并在前端新增组合筛选 UI。

## Glossary

- **Facet**: 可选筛选维度，取值来自插件输出（`mock.tags` / `mock.category`）或 `face_embeddings`（人物）。
- **PlaceCluster**: `/api/places` 返回的聚类项，含 `grid` 中心坐标、`count`、`asset_ids`。
- **active filter set**: 当前已启用的全部筛选条件集合，条件是 AND 关系。
- **asset cursor**: `/api/assets` 的分页游标（按 `id` 递减），用于无限滚动。

## Requirements

### Requirement 1: 组合筛选面板

**User Story:** AS 用户，I want 在时间轴页展开一个筛选面板，so that 能叠加多个条件精确定位照片。

#### Acceptance Criteria

1. WHEN 用户点击「筛选」按钮，THEN 系统 SHALL 显示/隐藏筛选面板。
2. WHEN 用户选择或修改任一筛选条件，THEN 系统 SHALL 立即以新的条件组合重新查询资产列表。
3. WHEN 存在已激活的筛选条件，THEN 系统 SHALL 在面板与顶部显示可清除的条件标识，且支持一键清除全部条件。

### Requirement 2: 媒体类型与收藏状态筛选

**User Story:** AS 用户，I want 按媒体类型和收藏状态筛选，so that 快速浏览照片/视频/收藏内容。

#### Acceptance Criteria

1. WHEN 用户选择媒体类型（图片/视频/全部），THEN 系统 SHALL 以 `media_type` 参数过滤结果。
2. WHEN 用户选择收藏状态（全部/已收藏/未收藏），THEN 系统 SHALL 以 `favorite=true|false` 参数过滤结果。

### Requirement 3: 日期范围筛选

**User Story:** AS 用户，I want 按拍摄日期范围筛选，so that 能浏览某时间段内的照片。

#### Acceptance Criteria

1. WHEN 用户设置开始/结束日期，THEN 系统 SHALL 以 `taken_after`/`taken_before`（epoch 秒）参数过滤 `Asset.taken_at`。
2. WHEN 日期范围变更，THEN 系统 SHALL 使用户设置的日期为本地时区 00:00/23:59:59 边界进行过滤。

### Requirement 4: 人物筛选

**User Story:** AS 用户，I want 按人物筛选，so that 查看某人的全部照片。

#### Acceptance Criteria

1. WHEN 用户从人物列表中选择一个人物，THEN 系统 SHALL 以 `person_id` 参数过滤 `face_embeddings` 归属该人物的资产。

### Requirement 5: 地点筛选

**User Story:** AS 用户，I want 按地点聚类筛选，so that 查看在某个 GPS 网格区域拍摄的照片。

#### Acceptance Criteria

1. WHEN 用户从地点聚类列表中选择一个聚类，THEN 系统 SHALL 以 `place` 参数（聚类 `grid` 坐标）过滤该网格单元内的资产。
2. WHEN 用户清除地点条件，THEN 系统 SHALL 恢复按其他条件筛选。

### Requirement 6: 标签筛选

**User Story:** AS 用户，I want 按标签筛选，so that 查看具有某标签的照片。

#### Acceptance Criteria

1. WHEN 用户从标签列表中选择一个标签，THEN 系统 SHALL 以 `tag` 参数过滤资产。
2. WHEN 标签列表为空（无标签数据），THEN 系统 SHALL 隐藏标签选择器并显示说明。

### Requirement 7: 结果展示与滚动加载

**User Story:** AS 用户，I want 筛选结果以现有网格展示并支持加载更多，so that 与时间轴浏览体验一致。

#### Acceptance Criteria

1. WHEN 筛选条件生效，THEN 系统 SHALL 使用 `JustifiedGrid` 展示结果，`useInfiniteQuery` 的 queryKey 包含完整筛选条件。
2. WHEN 滚动接近底部且存在下一页，THEN 系统 SHALL 通过 `cursor` 加载下一页。
3. WHEN 筛选结果为空，THEN 系统 SHALL 显示「无匹配资产」空态提示。

### Requirement 8: 日期与地点过滤的后端支持

**User Story:** AS 系统，I want 后端支持日期范围与地点过滤，so that 前端筛选面板能正确查询。

#### Acceptance Criteria

1. WHEN `/api/assets` 收到 `taken_after` 或 `taken_before` 参数，THEN 系统 SHALL 按 `Asset.taken_at` 过滤。
2. WHEN `/api/assets` 收到 `place` 参数（`"lat,lon"` 网格坐标），THEN 系统 SHALL 通过 EXIF GPS 聚类网格对齐后返回该网格单元内的资产。
3. WHEN 传递非法日期或无效地点参数，THEN 系统 SHALL 返回 422。
