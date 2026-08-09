# Requirements Document

## Introduction

HomeTrove 的 v1 必备视频能力「关键帧抽取」：对视频资产，基于 `basic.scene_detect` 的场景切分结果，为每个场景抽取 N 张关键帧（N 可配置），落盘为 JPEG 图片并记录每帧的时间戳。后端新增插件 `basic.keyframes` 与 `GET /api/assets/{id}/keyframes` 接口；前端在视频详情页展示关键帧条带，点击任一张关键帧将视频跳转到对应秒播放。

## Glossary

- **Scene**: `basic.scene_detect` 输出的一个视频片段，含 `start`/`end`（秒）与 `keyframe`（中点时间戳）。
- **Keyframe**: 从一个场景的时间范围内抽取的一帧图片，带独立时间戳。
- **Scene keyframes**: 每个场景按其配置 `per_scene` 抽取 N 帧，连同时间戳与文件路径记录在插件结果中。

## Requirements

### Requirement 1: 每场景抽取 N 张关键帧

**User Story:** AS 系统，I want 为视频每个场景抽取可配置数量的关键帧，so that 用户能预览每个场景的代表画面。

#### Acceptance Criteria

1. WHEN `basic.keyframes` 插件处理一个视频资产，THEN 系统 SHALL 为每个场景抽取 `per_scene` 张关键帧。
2. WHEN 场景的时间窗小于等于抽帧间隔，THEN 系统 SHALL 仍在该场景范围内取整段可用帧。
3. WHEN `per_scene=1`，THEN 系统 SHALL 优先取场景中点（`keyframe` 时间戳）。
4. WHEN `per_scene>1`，THEN 系统 SHALL 在场景 `[start, end]` 内均匀分布抽取时间点。

### Requirement 2: 无场景切分时的兜底

**User Story:** AS 系统，I want 在场景切分不可用时仍能输出关键帧，so that 视频不会因缺少场景结果而无关键帧。

#### Acceptance Criteria

1. WHEN 视频没有 `basic.scene_detect` 成功结果，THEN 系统 SHALL 以整个视频为单一时间窗抽取 `per_scene` 张关键帧。
2. WHEN 视频无法解码，THEN 系统 SHALL 记录 `skipped` 而非 `failed`。

### Requirement 3: 关键帧落盘与元数据

**User Story:** AS 系统，I want 关键帧图片落盘并记录时间戳，so that 前端能展示并支持跳转播放。

#### Acceptance Criteria

1. WHEN 关键帧生成成功，THEN 系统 SHALL 将 JPEG 写入 `{data_dir}/keyframes/{asset_id}/` 目录。
2. WHEN 记录插件结果，THEN 系统 SHALL 保存每个关键帧的场景索引、帧内索引、时间戳与文件路径。
3. WHEN 插件重复运行，THEN 系统 SHALL 覆盖同 asset 的关键帧文件与结果（幂等）。

### Requirement 4: 关键帧列表接口

**User Story:** AS 前端，I want 通过 API 获取资产的关键帧列表，so that 详情页能渲染关键帧条带。

#### Acceptance Criteria

1. WHEN 调用 `GET /api/assets/{asset_id}/keyframes`，THEN 系统 SHALL 返回该资产的关键帧列表（场景索引、帧内索引、时间戳、图片 URL）。
2. WHEN 资产不存在或已软删除，THEN 系统 SHALL 返回 404。
3. WHEN 资产尚无关键帧结果，THEN 系统 SHALL 返回空列表（HTTP 200）。

### Requirement 5: 关键帧图片服务

**User Story:** AS 前端，I want 通过 URL 获取单张关键帧图片，so that 缩略图条带能按需加载。

#### Acceptance Criteria

1. WHEN 调用 `GET /api/assets/{asset_id}/keyframes/{scene}/{index}`，THEN 系统 SHALL 返回对应的 JPEG 图片。
2. WHEN 对应关键帧文件不存在，THEN 系统 SHALL 返回 404。

### Requirement 6: 前端关键帧条带与跳转播放

**User Story:** AS 用户，I want 在视频详情页看到各场景的关键帧并能点击跳到对应画面，so that 快速浏览视频内容。

#### Acceptance Criteria

1. WHEN 视频详情页加载且有关键帧数据，THEN 系统 SHALL 在视频播放器下方显示横向滚动的关键帧条带。
2. WHEN 用户点击某一关键帧，THEN 系统 SHALL 将页面视频播放器跳转到该帧时间戳并播放。
3. WHEN 关键帧为空，THEN 系统 SHALL 隐藏条带区域。
