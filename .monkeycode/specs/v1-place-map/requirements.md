# Requirements Document

## Introduction

HomeTrove 的 v1 必备组织能力「地点地图」：把现有 `/api/places` 的 GPS 网格聚类数据渲染为交互式地图视图（Leaflet + OpenStreetMap），用户可在地图上缩放/平移、点击聚类圆点查看该地资产缩略图。现状（M1-8）已提供等距圆柱投影的静态 SVG 散点图和聚类列表，本需求将其升级为可交互地图，并保留资产缩略图网格。

## Glossary

- **PlaceCluster**: `/api/places` 返回的聚类项，含 `grid`（网格中心）、`lat`、`lon`、`count`、`asset_ids`。
- **Leaflet**: 开源交互式地图库（v1.9.x）。
- **OSM**: OpenStreetMap 瓦片服务，作为 Leaflet 底图来源。
- **聚类粒度（grid）**: 聚类网格边长（度），默认 0.5°，可经 `/api/places?grid=` 调整。

## Requirements

### Requirement 1: 交互式地图渲染

**User Story:** AS 用户，I want 在「地点」页看到可缩放平移的交互式地图，so that 能直观浏览照片的地理分布。

#### Acceptance Criteria

1. WHEN 地点页加载，THEN 系统 SHALL 使用 Leaflet 渲染 OpenStreetMap 底图并将每个 PlaceCluster 渲染为圆形标记。
2. WHEN 用户滚动缩放或拖拽地图，THEN 系统 SHALL 更新地图视口且标记位置保持与经纬度对齐。
3. WHEN 地图上的聚类标记被点击，THEN 系统 SHALL 选中该聚类并在下方显示其资产缩略图网格。

### Requirement 2: 聚类标记可视化

**User Story:** AS 用户，I want 标记大小与数量成比例，so that 能快速判断各地点照片密度。

#### Acceptance Criteria

1. WHEN 渲染聚类标记，THEN 系统 SHALL 根据 `count` 决定标记半径（大聚类更大）。
2. WHEN 聚类被选中，THEN 系统 SHALL 高亮该标记并在标记内显示 `count` 数值。

### Requirement 3: 资产缩略图网格

**User Story:** AS 用户，I want 点击聚类后查看该地照片，so that 能浏览特定地点的内容。

#### Acceptance Criteria

1. WHEN 聚类被选中，THEN 系统 SHALL 显示该聚类内资产（按 `asset_ids`）的缩略图网格。
2. WHEN 缩略图被点击，THEN 系统 SHALL 跳转到对应资产详情页。
3. WHEN 聚类资产超过一屏，THEN 系统 SHALL 在网格内提供分页或滚动加载。

### Requirement 4: 聚类列表保留

**User Story:** AS 用户，I want 保留现有聚类列表视图，so that 能按数量排序快速定位地点。

#### Acceptance Criteria

1. WHEN 地点页渲染，THEN 系统 SHALL 保留「全部聚类」列表，显示坐标与数量，点击与地图选中状态联动。
2. WHEN 列表项被点击，THEN 系统 SHALL 选中对应聚类并同步高亮地图标记。

### Requirement 5: 无 GPS 数据降级

**User Story:** AS 用户，I want 在库中没有带 GPS 坐标的照片时看到友好提示，so that 不会看到空白地图。

#### Acceptance Criteria

1. WHEN 无任何 PlaceCluster，THEN 系统 SHALL 显示当前的空态提示而非空地图。
2. WHEN `/api/places` 请求失败，THEN 系统 SHALL 显示错误提示并允许重试。
