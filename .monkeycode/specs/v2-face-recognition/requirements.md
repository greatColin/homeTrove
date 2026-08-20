# Requirements Document: 人脸识别 v2（三层数据模型 + 双插件架构）

## Introduction

当前 HomeTrove 的人脸识别管线存在三个根本问题：

1. `FaceEmbedding` 表没有 `source_plugin_id` 字段——一旦换识别模型，所有历史向量会与新向量混在一起匹配，导致全员崩盘。
2. `Person` 同时承担"插件输出"和"用户认知实体"两种角色，混合在一起导致无法区分"插件识别出的"和"用户命名的人"。
3. `mock.*` 占位插件混在真实插件中，无法表达"已实现"与"占位"的差异（虽然 v2 插件分类已上线，但仍实现仍依赖 mock）。

本次重构按用户视角建立三层数据模型（人员 → 识别组 → 人脸），用两个独立插件（`face.image` / `face.video`）替换占位与旧实现，引入模型下载机制（启动时检查 InsightFace 模型，缺失则在 UI 提供下载按钮），并提供可扩展的人工关联（跨插件同一人合并）能力。本设计**不考虑历史数据迁移**——开发阶段，所有旧插件（`face.detect`、`face.match`、`mock.*`）标记为 stub 并停止调度，新数据由新管线生成。

## Glossary

- **人员（Person）**：用户视角的最高层实体，对应现实中的某个人。包含一个或多个识别组。
- **识别组（FaceCluster）**：单一插件识别出的"同一人多张脸"的中间抽象。`source_plugin_id` 标识来源插件；UI 上按插件媒体类型显示为"图片识别组"或"视频识别组"。
- **人脸（Face）**：单次检测结果（一张图中的某张脸），属于某个识别组。包含向量、bbox、置信度、对应资产。
- **插件内自动归组**：单一插件内部使用 cosine 相似度将多张人脸自动聚簇为一个识别组（`min_faces` 阈值后才创建组）。跨插件不互相匹配。
- **跨插件手动关联**：用户主动把不同插件生成的两个识别组关联到同一个 Person。
- **代表脸（representative_face）**：每个识别组选出的"最佳显示用脸"，选取规则为组内质量最高的单张脸。
- **模型下载**：InsightFace `FaceAnalysis.prepare()` 自带的模型下载行为；插件启动时若检测到模型缺失，将状态置为 error 并提供 UI 入口触发异步下载。

## Requirements

### Requirement 1：双插件实现

**User Story：** AS a 用户，我需要 HomeTrove 启动后能自动识别图片与视频中的人脸，并按媒体类型分别归组，以便不同来源的素材有独立的人脸档案。

#### Acceptance Criteria

1. WHEN `face.image` 插件对一张图片执行时，THE 系统 SHALL 调用 InsightFace `FaceAnalysis.get()` 检测图中所有人脸并输出每张脸的 `embedding`、`confidence`、`bbox`。
2. WHEN `face.video` 插件对一个视频执行时，THE 系统 SHALL 从视频中抽取至多 8 帧（参数可配），对每帧独立调用 InsightFace `FaceAnalysis.get()`，合并全部检测结果并按帧时间戳记录。
3. WHILE 处理同一视频内的多帧，THE 系统 SHALL 对帧间同人脸去重（视频内同一人脸只保留一次代表）。
4. WHEN 任一插件的 `startup()` 检测到 InsightFace 模型缺失（`~/.insightface/models/buffalo_l/` 不存在），THE 系统 SHALL 将该插件状态置为 `error` 并在 `detail` 中包含"缺少模型"说明。
5. WHEN `face.image` 与 `face.video` 同时启用，THE 系统 SHALL 共享同一份内存中的 InsightFace `FaceAnalysis` 实例（引用计数管理），任一插件关闭不影响另一插件。
6. IF 视频帧内未检测到人脸，THE 系统 SHALL 跳过该帧继续处理其他帧，并返回空 `faces` 列表。
7. IF 单张图未检测到人脸，THE 系统 SHALL 返回空 `faces` 列表且 `status = "ok"`。

### Requirement 2：插件内自动聚簇

**User Story：** AS a 用户，我希望每个插件自动把识别出的同一人脸归为一组（无需我手动操作），以便我直接面对"识别组"层级。

#### Acceptance Criteria

1. WHEN 一次插件执行输出 N 张人脸时，THE 系统 SHALL 对每张脸计算其与所有现有未关联识别组的 `centroid` 的 cosine 距离。
2. IF cosine 距离 ≤ `merge_threshold`（默认 0.75），THE 系统 SHALL 把这张脸并入匹配得分最高的现有识别组，并更新该组的 `centroid`（均值向量）。
3. IF 没有识别组的 `centroid` 与新脸的 cosine 距离 ≤ `merge_threshold`，THE 系统 SHALL 创建一个新的未关联识别组，并把该脸作为首个成员。
4. WHILE 一个识别组的成员数 < `min_faces`（默认 3），THE 系统 SHALL 不为该组生成 `representative_face_id`（视为噪声组）。
5. WHILE 一个识别组的成员数 ≥ `min_faces`，THE 系统 SHALL 选取组内 `confidence × area` 评分最高的单张脸作为 `representative_face_id`。
6. WHEN 一张脸被并入某识别组，THE 系统 SHALL 持久化 `face.cluster_id` 与 `face.embedding_json` 字段。

### Requirement 3：人员管理

**User Story：** AS a 用户，我可以从识别组列表中创建"人员"，把多个识别组（来自不同插件）关联到同一个人，以便跨媒体类型聚合同一个真实人物。

#### Acceptance Criteria

1. WHEN 用户新建人员时，THE 系统 SHALL 创建一个 `Person` 行（`name`、`info_json`），初始不关联任何识别组。
2. WHEN 用户编辑人员姓名时，THE 系统 SHALL 调用 `PATCH /api/persons/{id}` 更新 `Person.name`，并对 `info_json` 做合并。
3. WHEN 用户把一个识别组拖拽到某人员详情页时，THE 系统 SHALL 调用 `PATCH /api/clusters/{cluster_id}` 将 `cluster.person_id` 设为该人员。
4. IF 一个 `Person` 被删除，THE 系统 SHALL 将其下属所有 `FaceCluster` 的 `person_id` 置为 NULL（保留识别组为未关联）。
5. WHEN 同一人员的两个识别组在 UI 上手动点击"合并"，THE 系统 SHALL 调用 `POST /api/clusters/{src_cluster_id}/merge-into/{dst_cluster_id}`，把源组所有 `Face` 转移到目标组，删除源组。

### Requirement 4：三层页面下钻

**User Story：** AS a 用户，我的人员列表页 → 人员详情页 → 识别组详情页 → 人脸详情页可以逐步看到更细粒度的信息，每层都标注"来自哪个插件"。

#### Acceptance Criteria

1. WHEN 用户访问 `/persons`，THE 系统 SHALL 列出所有人员，每行显示姓名、人脸总数、来自的插件数（去重）。
2. WHEN 用户点击某人员，THE 系统 SHALL 跳转到 `/persons/{id}`，按插件来源分组显示识别组列表，每个识别组项包含插件名称标签（"图片识别组 #N" / "视频识别组 #N"）。
3. WHEN 用户点击某识别组，THE 系统 SHALL 跳转到 `/persons/{id}/clusters/{cluster_id}`，显示该组所有人脸缩略图，每张人脸标注对应资产缩略图、置信度、bbox 可视化。
5. WHEN 用户访问 `/clusters?unassigned=true`，THE 系统 SHALL 列出全部未关联识别组（含来源插件标识），供用户拖拽或指派。
6. WHEN 任一层缺数据，THE 系统 SHALL 显示友好的空状态文案（如"暂无人员，先创建一位"、"此识别组暂无未关联人脸"）。

### Requirement 5：模型下载与启动重试

**User Story：** AS a 用户，如果人脸识别插件启动时发现缺少 InsightFace 模型，我希望看到清晰的提示，并能一键下载模型后自动重启插件。

#### Acceptance Criteria

1. WHEN `face.image` 或 `face.video` 在 `startup()` 阶段检测到 `~/.insightface/models/buffalo_l/` 目录缺失，THE 系统 SHALL 捕获 `FaceAnalysis.prepare()` 抛出的异常并将插件状态置为 `error`，`detail` 字段包含字符串"缺少模型"。
2. WHEN 插件列表页显示某个插件状态为 `error` 且 `detail` 包含"缺少模型"，THE 系统 SHALL 在该行内显示"下载模型并重启"按钮。
3. WHEN 用户点击该按钮，THE 系统 SHALL 调用 `POST /api/plugins/{plugin_id}/download-model`，启动后台异步任务下载模型（通过一次性调用 `FaceAnalysis.prepare()` 触发 InsightFace 自带的下载机制）。
4. WHILE 后台任务正在下载模型，THE 系统 SHALL 显示"下载中"占位状态并轮询插件状态。
5. WHEN 模型下载完成后，THE 系统 SHALL 自动调用 `startup_plugin(plugin_id)` 重启插件，并将状态从 `loading` 转到 `active` 或 `error`。
6. IF 下载过程中网络失败，THE 系统 SHALL 把任务状态标记为失败，错误信息回显到 UI 供用户重试。
7. WHEN `face.image` 与 `face.video` 都因模型缺失而处于 `error` 状态，THE 系统 SHALL 在任一插件上点击"下载模型"都触发共享下载（首次成功后另一插件也自动可启动）。

### Requirement 6：插件 status 联动

**User Story：** AS a 用户，我希望 `face.image` 与 `face.video` 在系统中作为独立的插件条目出现，各自可启停、独立显示状态。

#### Acceptance Criteria

1. WHEN 插件注册时，THE 系统 SHALL 把 `face.image` 和 `face.video` 注册为两个独立的 `BasePlugin` 实例，分别有独立的 `id`、`name`、`description`。
2. WHEN 用户访问 `/settings/plugins`，THE 系统 SHALL 在列表中分别显示这两个插件及其各自的状态、启停开关。
3. WHEN `face.image` 被禁用，THE 系统 SHALL 调用 `shutdown_plugin("face.image")` 释放其引用；IF `face.video` 仍处于启用状态，THE 系统 SHALL 保留其 InsightFace 实例不被释放。
4. WHEN `face.image` 与 `face.video` 都被禁用，THE 系统 SHALL 完全释放 InsightFace `FaceAnalysis` 实例与模型文件内存占用。
5. WHEN `face.video` 被禁用后再启用，THE 系统 SHALL 复用已有 InsightFace 实例（不再重新加载模型），引用计数 +1。

## Out of Scope

- **历史数据迁移**：开发阶段不考虑迁移现有 `Person` / `FaceEmbedding` 数据。旧插件标记 stub 后停止调度，新管线生成新数据。
- **跨插件自动匹配**：不允许插件之间互相匹配向量；仅靠用户手动关联。
- **多模型并行**：InsightFace `FaceAnalysis` 同一时刻只装载一个 pack。Phase 1 仅 `buffalo_l`，未来 `buffalo_s`/`antelopev2` 通过切换 pack 实现（切换会触发 InsightFace 实例重新加载）。
- **ANN 索引**：Phase 1 不引入 FAISS / sqlite-vec；使用 brute-force cosine + centroid 二级匹配，量级 < 5万脸时性能可接受。
- **模型离线下载 / 代理**：依赖 InsightFace 自带的网络下载，不实现手动 URL 上传模型。
- **人脸质量评分**：不在 Phase 1 实现；视频插件使用 `confidence` 与帧质量启发式。
- **批量合并人员**：不允许一次合并 3 个及以上 Person；仅支持两两合并。