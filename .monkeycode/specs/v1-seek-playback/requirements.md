# Requirements Document

## Introduction

HomeTrove 的 v1 必备视频能力「播放器跳秒」：让视频播放器能按场景切分点快速跳转。具体包括两部分：(1) 视频详情页播放器叠加场景切分点 scrubber，点击某场景段跳转到该场景起点播放；(2) 详情页支持 `?t=seconds` URL 参数，从搜索结果等入口进入时自动跳转到对应秒播放。搜索命中 lightbox 的跳秒已具备，本需求补齐「时间轴场景切分点 scrubber」与「从详情页 URL 进入跳秒」。

## Glossary

- **Scene**: `basic.scene_detect` 插件输出的场景分段，含 `start`/`end`（秒）。
- **Scrubber**: 视频进度条下方叠加的场景分段指示条，分段可点击跳转。
- **`?t=`**: 详情页 URL 查询参数，视频加载后跳到指定秒。

## Requirements

### Requirement 1: 详情页场景切分点 scrubber

**User Story:** AS 用户，I want 在视频详情页看到场景分段条并能点击跳转，so that 快速定位到想看的片段。

#### Acceptance Criteria

1. WHEN 视频详情页加载且该视频存在 `basic.scene_detect` 场景结果，THEN 系统 SHALL 在播放器下方渲染场景分段指示条。
2. WHEN 用户点击某一场景分段，THEN 系统 SHALL 将播放器跳转到该场景的 `start` 秒并播放。
3. WHEN 视频没有场景切分结果，THEN 系统 SHALL 不渲染 scrubber。

### Requirement 2: 详情页 `?t=` 跳秒

**User Story:** AS 系统，I want 详情页支持通过 URL 指定跳转秒，so that 搜索结果等入口能把用户带到命中位置。

#### Acceptance Criteria

1. WHEN 详情页 URL 带 `?t=<秒>` 且资产为视频，THEN 系统 SHALL 在视频元数据加载完成后跳转到该秒。
2. WHEN 跳秒值非法（非数字 / 负数 / 超过时长），THEN 系统 SHALL 忽略并正常播放。
3. WHEN 资产为图片或 `?t` 缺失，THEN 系统 SHALL 不触发跳秒。

### Requirement 3: 搜索结果详情跳转带时间戳

**User Story:** AS 用户，I want 从搜索结果进入视频详情时直接从命中片段开始，so that 点击「详情」不丢失命中位置。

#### Acceptance Criteria

1. WHEN 搜索命中为视频场景（`can_seek` 且 `t_start` 存在），THEN 系统 SHALL 在「详情」链接中携带 `?t=<t_start>`。
2. WHEN 搜索命中为图片或视频封面命中，THEN 系统 SHALL 不携带时间戳参数。

### Requirement 4: 播放器跳秒正确性

**User Story:** AS 系统，I want 跳秒动作可靠生效，so that 用户能稳定看到指定片段。

#### Acceptance Criteria

1. WHEN 播放器已挂载且元数据可加载，THEN 系统 SHALL 通过 `currentTime` 设置跳转秒。
2. WHEN 跳转后播放器未自动播放（浏览器限制），THEN 系统 SHALL 保持 `controls` 让用户手动播放，不报错。
