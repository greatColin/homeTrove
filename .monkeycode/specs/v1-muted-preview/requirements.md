# Requirements Document

## Introduction

HomeTrove 的 v1 必备视频能力「视频栅格内静音预览播放（桌面 hover）」：在时间轴（及使用同一缩略图网格的视频场景）中，当用户把鼠标悬停到视频缩略图上时，用静音视频原地播放预览；移开鼠标后停止并恢复缩略图。纯前端能力，复用既有 `/api/assets/{id}/file` 视频流接口，不引入新依赖。

## Glossary

- **Hover preview**: 鼠标悬停在缩略图上时以 `<video muted autoplay loop playsInline>` 原地播放视频预览。
- **GridCell**: 时间轴 justified 网格中的单个资产单元（`web/src/routes/timeline.tsx`）。

## Requirements

### Requirement 1: 网格视频 hover 播放预览

**User Story:** AS 桌面用户，I want 把鼠标悬停到视频缩略图上就能看到静音预览，so that 不用点开就能了解视频内容。

#### Acceptance Criteria

1. WHEN 用户在桌面端把鼠标悬停到一个视频缩略图上，THEN 系统 SHALL 以静音视频原地播放该视频。
2. WHEN 鼠标移开该缩略图，THEN 系统 SHALL 停止播放并恢复显示缩略图。
3. WHEN 缩略图对应的是图片资产，THEN 系统 SHALL 不做任何预览播放。

### Requirement 2: 预览与现有交互兼容

**User Story:** AS 用户，I want hover 预览不影响点击、选择和收藏，so that 现有操作保持不变。

#### Acceptance Criteria

1. WHEN 用户点击一个正在预览的视频缩略图，THEN 系统 SHALL 触发与未预览时一致的点击行为（打开灯箱 / 进入选择模式）。
2. WHEN 选择模式下视频缩略图被悬停，THEN 系统 SHALL 同时显示选中标识与预览播放。
3. WHEN 视频无法播放（浏览器不支持/文件损坏），THEN 系统 SHALL 静默隐藏预览，缩略图保持可见。

### Requirement 3: 性能约束

**User Story:** AS 系统，I want hover 预览不拖慢大库滚动，so that 虚拟滚动仍保持流畅。

#### Acceptance Criteria

1. WHEN 视频缩略图不在鼠标所在单元，THEN 系统 SHALL 不加载其视频数据（`preload` 仅在悬停时加载）。
2. WHEN 鼠标离开后，THEN 系统 SHALL 及时停止播放并释放资源。
3. WHEN 大量视频同时存在，THEN 系统 SHALL 仅播放当前悬停单元的视频。
