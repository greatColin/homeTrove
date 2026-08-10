# Requirements Document

## Introduction

HomeTrove 的 M1-5 中文描述能力：新增 `vlm.qwen3vl` 插件，对图片生成中文自然语言描述、对视频按场景关键帧生成带时间戳的描述；输出形制 `{"captions": [{"t", "caption"}]}`。在此基础上把描述文本纳入语义管道：新增 `embedding.bge_m3` 文本向量插件，将 VLM 输出描述编码为 `scope="caption"` 的向量写入 `embeddings` 表与 sqlite-vec 索引，使搜索能通过 `scope:caption` 按描述召回并跳秒。

## Glossary

- **Caption**: VLM 生成的某张图片或某个视频场景的自然语言中文描述，带可选时间戳 `t`。
- **VLM**: 视觉语言模型（本插件对 `vlm.qwen3vl`），负责"看懂画面并说出内容"。
- **Caption scope**: `embeddings.scope` 的一个取值（`caption`），表示该向量由描述文本编码而来。

## Requirements

### Requirement 1: 图片中文描述

**User Story:** AS 系统，I want 为图片资产生成中文自然语言描述，so that 用户能按画面内容搜索图片。

#### Acceptance Criteria

1. WHEN `vlm.qwen3vl` 插件处理一个图片资产，THEN 系统 SHALL 返回含一条 caption 的结果，其中 `t` 为 `null`。
2. WHEN VLM 模型不可用（未配置端点 / 加载失败），THEN 系统 SHALL 回退到确定性 mock 描述，输出形制与真实模型一致。
3. WHEN 插件重复运行同一资产，THEN 系统 SHALL 返回相同的 mock 描述（确定性）。

### Requirement 2: 视频按场景描述

**User Story:** AS 系统，I want 为视频的每个场景关键帧生成中文描述，so that 视频画面内容可被逐场景检索并跳秒。

#### Acceptance Criteria

1. WHEN `vlm.qwen3vl` 插件处理一个视频资产，THEN 系统 SHALL 对 `basic.scene_detect` 的每个场景（最多 `max_scenes` 个）生成一条 caption。
2. WHEN 一条 caption 对应某个场景，THEN 系统 SHALL 将其 `t` 设为该场景的 `keyframe` 时间戳（秒）。
3. WHEN 视频没有 scene_detect 结果，THEN 系统 SHALL 以单个时间点 `0.0` 生成一条兜底 caption。

### Requirement 3: 非目标媒体跳过

**User Story:** AS 系统，I want 对不支持的媒体类型不产生结果，so that 不会污染其他资产。

#### Acceptance Criteria

1. WHEN 资产不是图片或视频（`other`），THEN 系统 SHALL 返回 `skipped` 并说明原因。
2. WHEN 资产源文件缺失，THEN 系统 SHALL 返回 `skipped`。

### Requirement 4: 真实模型接入形态

**User Story:** AS 运维，I want 通过配置指定 VLM 端点，so that 环境具备模型时可切换到真实推理。

#### Acceptance Criteria

1. WHEN `backend="mock"`，THEN 系统 SHALL 强制使用确定性 mock 描述。
2. WHEN `backend="qwen3vl"`，THEN 系统 SHALL 使用真实 VLM 端点；若端点不可达或调用失败，THEN 系统 SHALL 返回 `skipped`（不静默回退）。
3. WHEN `backend="auto"`（默认），THEN 系统 SHALL 优先真实端点，不可用时回退 mock。

### Requirement 5: 描述文本进入 caption scope 向量

**User Story:** AS 系统，I want 将 VLM 描述文本编码为语义向量，so that 搜索可按描述内容召回资产。

#### Acceptance Criteria

1. WHEN `embedding.bge_m3` 插件处理一个资产，THEN 系统 SHALL 读取同资产 `vlm.qwen3vl` 的最新成功结果，并为每条 caption 写入一条 `scope="caption"` 的 `Embedding` 行。
2. WHEN caption 带时间戳 `t`，THEN 系统 SHALL 将 `t` 写入该向量行的 `t_start`/`t_end`，使视频命中可跳秒。
3. WHEN 资产没有 `vlm.qwen3vl` 成功结果，THEN 系统 SHALL 返回 `skipped`。
4. WHEN 插件重复运行，THEN 系统 SHALL 先删除该插件旧的 caption 向量再写入（幂等）。

### Requirement 6: 按描述检索

**User Story:** AS 用户，I want 用中文描述文字搜索画面内容，so that 能找到记忆中的瞬间。

#### Acceptance Criteria

1. WHEN 搜索查询带 `scope:caption` 前缀，THEN 系统 SHALL 仅从 caption scope 向量召回。
2. WHEN 未指定 scope，THEN 系统 SHALL 将 caption 向量与其他 scope 一并参与默认混合召回。
3. WHEN 视频的 caption 命中且带 `t_start`，THEN 系统 SHALL 返回 `can_seek=true` 以便前端跳秒。

### Requirement 7: 插件管理与依赖顺序

**User Story:** AS 运维，I want 两个插件按依赖顺序入队且可单独启停，so that 插件管理与重跑机制统一生效。

#### Acceptance Criteria

1. WHEN 构建 DAG，THEN `vlm.qwen3vl` SHALL 依赖 `basic.scene_detect`（视频场景）与 `basic.info`。
2. WHEN 构建 DAG，THEN `embedding.bge_m3` SHALL 依赖 `vlm.qwen3vl`。
3. WHEN 任一插件被禁用，THEN worker 的 `enqueue_pending`/`_claim_next` SHALL 跳过其作业。
