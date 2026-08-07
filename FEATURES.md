# HomeTrove 功能清单与开发进度

> **这是全项目唯一的功能进度清单**。README 只保留产品定位与架构说明；所有「预计功能、当前进度、验收口径」以本文件为准。
> 约定：完成一项就勾选一项 `[x]`，并在括号里标注完成日期。当日完成的功能当日勾。

## 图例

- `[x]` 已完成（附完成日期）
- `[ ]` 未完成 / 待实现
- **依赖**：该项的前置功能，未完成前不开始本项
- **验收**：判定完成的最低口径（人工 + 自动化）

---

## 阶段总览

| 阶段 | 主题 | 状态 |
|---|---|---|
| **M0** | 基础框架：扫描 → 入库 → 浏览/上传闭环 | ✅ 完成（2026-08-04） |
| **M1** | 插件扩展：真实内容识别 + 检索 + 分类页 + 插件管理 | ⏳ 进行中 |
| **v1.1** | 多用户 / 共享 / ASR / 移动端等（见下） | ⬜ 未开始 |
| **v2** | 远期能力（见下） | ⬜ 未开始 |

> M0 完成时额外的「人脸分组」与「标签/分类页」已提前部分落地（见 M1-4、M1-8），因为当时用模拟插件先把前端页做出来了。

---

## M0 基础框架（✅ 已完成，2026-08-04）

M0 目标：不依赖任何 AI 模型，跑通「扫描 → 入库 → 浏览/上传」最小闭环。只读媒体根、增量扫描、`content_hash` 去重、DAG 调度骨架、时间轴/文件夹/任务中心页面。

| # | 任务 | 状态 | 验收口径 |
|---|---|---|---|
| M0-1 | 仓库骨架（`hometrove/` + `web/` + `pyproject.toml`） | [x] 2026-08-04 | `pip install -e .` 后 `hometrove api/worker/serve` 可启动 |
| M0-2 | SQLite(WAL) + SQLAlchemy 2.0 + Alembic，schema：`assets/plugin_results/jobs/plugin_config` | [x] 2026-08-04 | `upgrade head` 成功；二次启动幂等 |
| M0-3 | 媒体根只读校验（启动时 warning，v1.1 升级强制） | [x] 2026-08-06 | 手动 `chmod -w` 后启动日志有明确提示 | **`hometrove/readonly.py`**：每个 `media_roots_paths` 写 1 字节 sentinel 后立即 unlink；可写 → `WARNING` + 「remount -o ro」提示；只读 → `INFO`；缺失 / 非目录 / `os.access(W_OK)=false` 结构化 skip；root 用户特殊处理（chmod 检查 DAC 是否旁路，旁路则同样报只读）；`HOMETROVE_READ_ONLY_CHECK=warn|off`（默认 warn）；`create_app` lifespan / `hometrove scan` CLI / `hometrove worker` 三处启动路径接入；9 个新测试覆盖可写、只读（mocked os.access）、root DAC 旁路、缺失路径、非目录、`off` 豁免、`run_for_settings` 警告、lifespan 集成，79 测试全绿 |
| M0-4 | 扫描器：初始全量 + 轮询增量 + `content_hash` 去重 | [x] 2026-08-04 | 同目录扫两次行数不变；新文件可被发现 |
| M0-5 | `basic.info` 插件（name/media_type/size/mtime/hash，尽力读宽高/时长/拍摄时间） | [x] 2026-08-04 | 每资产一行 `plugin_results` 且 `status='ok'` |
| M0-6 | Worker + DAG 拓扑调度（`depends_on`） | [x] 2026-08-04 | 插件严格走完 DAG prepare/running/done |
| M0-7 | SSE 进度推送（worker → api → 前端） | [x] 2026-08-04 | `/settings/jobs` 实时看到「处理 X / 共 Y」 |
| M0-8 | REST API：`/api/assets` `/api/folders` `/api/health` `/api/jobs` | [x] 2026-08-04 | curl 验证 schema；OpenAPI 自动生成 |
| M0-9 | 前端骨架（React 19 + Vite + Tailwind + TanStack Query） | [x] 2026-08-04 | 构建产物可被服务托管 |
| M0-10 | `/timeline` 时间轴（**M0 用占位色块，无实缩略图**） | [x] 2026-08-04 | 千级网格流畅滚动 |
| M0-11 | `/folders` 原始目录树 | [x] 2026-08-04 | 点开任意目录正常加载子节点 |
| M0-12 | `/settings/jobs`（进度条 + 队列 + 失败重试） | [x] 2026-08-04 | 制造失败 job 可页面重试 |
| M0-13 | 纯 Python 单进程 `hometrove serve`（Docker 降级为可选） | [x] 2026-08-04 | 单命令可达 `/api/health` 与前端 |
| M0-14 | 文档 `docs/INSTALL.md` + `docs/DEV.md` | [x] 2026-08-04 | 新人 20 分钟内能起本地实例 |

### M0 超出清单的额外交付（也都已完成）

- 分片上传 / 断点续传 / 幂等重传 / sha256 合并（`/api/uploads/*`）
- 通用入队 `enqueue_pending`（对所有缺结果的插件幂等入队）
- 任务中心按文件聚合展示 + 单任务重试
- 3 个**模拟**插件 `mock.tags` / `mock.category` / `mock.faces`（确定性，供前端开发与测试）
- `/api/facets` 聚合 + 按 tag/category/person 过滤资产
- 文件详情页 `/asset/:id`（动态字段渲染全部插件结果）
- **人脸分组系统**（本应属 M1-4，已提前实现，见 M1-4 说明）

### M0 明确不交付（推迟到 M1，避免范围蔓延）

缩略图插件、EXIF、场景切分、真实人脸 / VLM / 向量 / ASR、语义搜索、地图/相册/真实标签、`/settings/plugins` 页面、鉴权 / 多用户、ASR / HEIC / Live Photo / RAW。

---

## M1 插件扩展（⏳ 进行中）

在稳定框架上陆续接入真实插件。每条对应一份 RFC，**按序号顺序推进**，依赖未完成前不开始下一项。

| # | 功能 | 状态 | 依赖 | 说明 / 验收 |
|---|---|---|---|---|
| M1-1 | `thumbnail` 缩略图插件 | [x] 2026-08-05 | M0 完成 | **纯 Python 0.2.0**：Pillow 多档位（small 320 / medium 1280）写 `{data_dir}/thumbs/{asset_id}/`；视频用 PyAV（自带 FFmpeg 库）抽帧 → numpy → Pillow，解码失败才回退占位图；EXIF transpose 自动旋转；前端网格改用 `/api/assets/{id}/thumbnail?size=` 真实缩略图 |
| M1-2 | `exif` 插件 | [x] 2026-08-05 | M0 | **纯 Python 0.2.0（已替换 exiftool）**：图片用 Pillow `getexif()` 读相机/镜头/ISO/曝光/焦距/GPS（GPS IFD 转十进制 `gps_lat/gps_lon`）；视频用 PyAV 读 duration/codec/分辨率/fps/rotation/encoder；详情信息栏动态渲染；无 EXIF 时 metadata 为空但 status=ok |
| M1-3 | `basic.scene_detect` 视频场景切分 | [x] 2026-08-05 | M0 | **纯 Python 0.1.0**：PySceneDetect ContentDetector + `pyav` 后端（scenedetect 自带 opencv 回退）；输出场景起止秒 + `keyframe` 时间戳；结果经 `get_asset.plugin_results` 暴露，供 M1-4/M1-5 共享 |
| M1-4 | `face.detect` 真实人脸 | [x] 2026-08-05 | M1-3（视频） | **InsightFace buffalo_l（SCRFD 检测 + ArcFace 512 维）on CPU（onnxruntime）**；模型包首次自动下载（唯一非 pip 组件）；图片全帧检测，视频读 `basic.scene_detect` keyframe 抽帧 + 同人 cosine 去重；输出 `{faces:[{embedding,confidence,box}]}` 供 `face.match` 归组；无模型/无 insightface 时 skipped。**说明**：归组管线 M0 已就绪（`face.match` + `/api/persons` 命名反扫/合并），真实检测器就位后直接复用，`faces` 表即既有 `face_embeddings` |

#### 人脸归组方案（当前实现，2026-08-05 备注）

实现：`face.detect`（检测）→ `face.match`（归组）→ `hometrove.faces`（匹配核心）。匹配函数：`cosine_similarity`（faces.py）+ `_best_person` / `match_face` / `match_asset_faces` / `name_person_and_backfill` / `merge_persons`。

- **每张照片都存人脸，不去重**：`face.match` 为资产中**每个**检测到的人脸各写一条 `face_embeddings` 记录（N 张照片 = N 条向量）。仅视频**内部**跨帧去重（同人 cosine ≥0.45 跳过，`face.detect` 的 `video_dedup_threshold`），跨照片不去重。
- **比对目标：库中任意一张脸**，无「主要人脸」概念：`_best_person` 把新向量与库中**全部已存 embedding**逐一算余弦相似度，取全局最高分且 ≥阈值（默认 0.75，`face.match` ParamsModel 可调）者。
- **不会同时归属两个人**：`_best_person` 遍历全部取最优（非"首个过阈值即返回"），即使与 A、B 两人都过阈值也只归入相似度更高者；全局最高分未过阈值 → 新建 `未命名-<rand>` person。一个向量永远只归属一个 person。
- **命名反扫**：`name_person_and_backfill` 用该人现有向量均值作代表向量，扫所有 `未命名*` person 中余弦相似度 ≥0.75 的脸拉入该人名下（"命名一次，把旧照片同人拉进来"）。
- **合并是手动显式操作**：`merge_persons`（API 层），绝不自动合并。
- **当前实现规模**：纯 Python 全库 O(N) 扫描比对，无向量索引；ANN 索引（sqlite-vec）为 M1-6 升级路径。
| M1-5 | `vlm.qwen3vl` 中文描述 | [ ] | M1-3（视频） | 接 VLM 端点；**前置已就位**：`PluginContext.image()/frames()/result_of()` 共享缓存已实现并测试（`resolve_asset_path` 统一路径解析；`face.detect` 已消费共享缓存） |
| M1-6 | `embedding.jina_clip` + `embedding.bge_m3` | [x] 2026-08-05（基础设施 + image/scene scope） | M1-5（caption scope） | sqlite-vec 接入；`embeddings` 表（scope=image/scene）；**当前为无模型 mock 向量阶段**（确定性哈希+颜色特征，1024 维单位向量），`_encode_image/_encode_frame` 后续原地替换为真实 jina-clip-v2 即得真实语义；**caption scope 待 M1-5 完成后追加**（视频 scene 向量已就位，caption 只需在 VLM 出文后写同 scope） |
| M1-7 | `/search` 语义搜索（双路召回 + RRF 融合） | [x] 2026-08-05（mock 向量阶段） | M1-6 | **已实现**：`/api/search?q=`（`hometrove/search.py`）；双路召回 = 向量（查询文本编码 1024 维查 `embedding_vec`，复用 `embedding.jina_clip` mock 编码器，真实 jina-clip-v2 文本编码器替换 `_encode_query` 即可）+ 关键词（SQL LIKE 匹配 `mock.tags`/`mock.category`/`exif`/`basic.info` 文本）；RRF（k=60）融合排序；`scope:scene/image` 前缀限定召回范围；视频 scene 命中带 `t_start/t_end` + `can_seek`，前端 `/search` 页从命中秒播放。**说明**：caption 文本召回待 M1-5 完成后并入关键词路径 |
| M1-8 | `/albums` `/tags` `/places` 分类页 | [x] 2026-08-05 | M0 | **已实现**：`/api/albums` CRUD + 资产添加/移除（`albums`/`album_assets` 表，position 排序、封面、删除级联）；前端 `/albums` 页（列表卡片 + 详情 + 新建 + 选择添加 + 设封面/移除/重命名/删除）；`/api/places` 按 exif GPS 网格聚类（grid 可调，均值坐标 + 资产列表）；前端 `/places` 页（等距圆柱投影散点地图 + 聚类列表 + 该地资产网格）；`/tags` `/categories` 沿用模拟页（真实标签数据就位后替换）；`/people` 已提前实现 |
| M1-9 | `/settings/plugins` 插件管理 | [x] 2026-08-05（完整） | M0 | **已完整落地**：`/api/plugins`（GET 列表含 `params` 当前值与 `params_schema` / PUT 开关 + 参数）；`enqueue_pending` 与 worker `_claim_next` 尊重 `plugin_config.enabled`（禁用插件不入队、队列中未运行的该插件 job 停驻、重启用恢复）；禁用时调用插件 `shutdown()` 释放内存（face.detect 释放 FaceAnalysis，磁盘 artifacts 不动）；**参数表单**：前端按 `params_schema` 自动渲染 boolean/number/string/enum 控件，保存时经 `ParamsModel.model_validate` 校验（非法 422）；**定向重跑**：`POST /api/plugins/{id}/rerun` 丢弃该插件 done/failed 任务并整库重新入队（禁用时拒绝 400），用于调整参数后重新应用；版本展示已有 |
| M1-10 | `asr.faster_whisper` 语音转写 + **上传插件预设** | [x] 2026-08-06 | M1-3 | **asr**：抽取视频音频（PyAV 解码 + AudioResampler → 16 kHz mono PCM WAV 落到 `plugin-tmp/{aid}/audio.wav`）；**双后端**：`backend=auto`（有 faster-whisper 用真模型 / 否则回退 mock）、`backend=mock`（强制 deterministic 字幕，2–4 段，hash-by-path 稳定）、`backend=faster_whisper`（要求包已装；不满足即 skipped）；`ParamsModel` 含 `language/beam_size/backend/min_confidence`；输出写入新表 `asr_transcripts`（asset_id+plugin_id+plugin_version+t_start/t_end/text/lang/confidence，alembic 0003，幂等覆盖版本数据）；`GET /api/assets/{id}` 暴露 `transcripts[]`；`/api/search` 关键词路径增加 transcript 召回（`scope=audio`，携带 `t_start/t_end/can_seek`，命中可直接跳秒）；`scope:audio` 限定召回源；首次安装默认 mock，装上 `faster-whisper` 即自动切真模型。9 个新测试覆盖 mock 产出、写入、幂等、跳过、real unavailable、详情页暴露、关键词召回、scope:audio 过滤、min_confidence 过滤，70 测试全绿。**上传插件预设**：上传时选预设（内置默认/会议/旅游 + 用户自定义），插件 checkbox 列表回填后可手动调整，最终 `plugin_ids` 列表随上传提交，后端 `enqueue_pending(plugin_ids=...)` 仅入队选中插件；`plugin_presets` 表含内置/自定义，支持 CRUD；`POST /api/uploads/{id}/ingest?plugin_ids=` |
| M1-11 | 鉴权骨架接入 | [x] 2026-08-06 | — | **`AuthBackend` 协议 + `Principal` dataclass**（`hometrove/auth.py`，含默认 `PassthroughAuthBackend` 与工厂 `build_auth_backend()`，预留 `passthrough/session/token/oidc` 命名空间）；ASGI 中间件 `AuthMiddleware`（`hometrove/api/middleware.py`）将 principal 挂到 `request.state.principal`；FastAPI 依赖项 `current_principal` / `require_auth` / `require_scope`（`hometrove/api/deps.py`）；`Settings` 新增 `auth_backend` / `auth_cookie_name` / `auth_session_ttl_seconds`（env 切换，不引入新依赖、不动模型层）；隐藏调试路由 `/api/_debug/principal` 仅暴露供测试可见；`create_app()` 注册中间件（早于所有路由），`app.state.auth_backend` 作为测试 seam 便于替换；4 个新测试覆盖默认放行、后端可替换、工厂回退、principal 暴露，61 测试全绿 |

### M1 完成判定（整体验收）

- 全文检索与向量检索同时启用，RRF 融合可观察
- 视频搜索命中点能从对应秒数播放
- 外行用户按文档可在 PR 中新增一个「描述类」插件，无需改主仓库其他文件
- `thumbnail / exif / scene_detect` 从关闭→打开后，`/settings/jobs` 能看到为存量资产自动补建 job

### M1 插件分发与模型权重策略（方向性约定）

- 插件包通过 Python `entry_points` 分发（`[project.entry-points."hometrove.plugins"]`）
- **模型权重绝不进 Git**：仓库只放清单（如 `models/jina-clip-v2.txt` 写 HF repo id + 版本）；`.gitignore` 排除 `*.bin/*.safetensors/*.onnx`
- 部署/首跑由 `hometrove model fetch` 从远端拉取并校验 SHA256，落盘 `/data/model-cache/`
- 插件只允许读取 `model-cache` 下的模型路径；CI 跑「fetch → load → inference」冷启动演练

---

## v1 必备（M1 完成后补齐的产品功能）

> 以下对应原 README「4.1 v1 必备」。标记 `[x]` 的表示 M0/M1 已覆盖或部分覆盖。

### 媒体库与浏览

- [x] 主时间轴（按年/月/日分组、右侧刻度条、缩放、滚动定位）—— **基础版已在 M0-10 完成**；justified 网格 / 虚拟滚动 / 缩放级别留待增强
- [x] 文件夹视图（原始目录树）—— M0-11 完成
- [x] 网格布局 justified（等高两端对齐）+ 视频缩略图代表帧—— **已完成，2026-08-07**：详见下方「v1 justified 网格 + 虚拟滚动」段
- [x] 虚拟滚动（10 万张无明显抖动）—— 同上段
- [x] 灯箱（大图浏览、键盘导航）—— 基础版已实现（timeline Lightbox）
- [x] 批量操作（多选 / 移动 / 收藏 / 添加到相册 / 软删除）—— **已完成，2026-08-07**：详见下方「v1 批量操作」段
- [x] 收藏（单资产标记）—— 同上段
- [x] 回收站（软删除 + 还原，默认保留 30 天）—— **已完成，2026-08-07**：详见下方「v1 回收站」段

### 组织

- [x] 手动相册（创建/重命名/排序/封面/描述）—— **已完成，2026-08-07**：详见下方「v1 共享相册」段
- [x] 共享相册（公开分享链接）—— **已完成，2026-08-07**：详见下方「v1 共享相册」段
- [x] 智能相册（v1 仅基于人脸/地点/标签的简单条件筛选，无可视化规则编辑器）—— **已完成，2026-08-07**：详见下方「v1 智能相册」段
- [x] 标签（自动 + 手动）—— 自动标签由 M1-5 的 VLM + M1-1 物体识别提供；页面已就绪
- [x] 人物（人脸聚类）—— **核心已完成**：命名/合并/过滤/JSON 信息（`/people` 页）；HDBSCAN 聚类算法待接入
- [x] 地点（GPS 聚合 + Leaflet + OpenStreetMap 地图视图）—— **已完成，2026-08-07**：详见下方「v1 地点地图」段

### 检索

- [x] 高级筛选（媒体类型/日期范围/人物/地点/标签/收藏状态）—— **已完成，2026-08-07**：详见下方「v1 高级筛选」段
- [x] 语义搜索 `/search`（双路 + RRF，视频片段跳秒）→ 即 M1-7
- [x] 以图搜图（v1 简化：从资产出发召回相似项）—— **已完成，2026-08-07**：详见下方「v1 以图搜图」段

### 视频特有

- [ ] 场景切分（PySceneDetect，参数可调）→ M1-3
- [ ] 关键帧抽取（每场景 N 张，N 可配置）
- [ ] 视频栅格内静音预览播放（桌面 hover）
- [ ] 播放器跳秒（命中片段从 `t_start` 播；时间轴场景切分点 scrubber）

### 索引与管理

- [x] 索引扫描（增量监听 + 启动全量 + hash 去重）—— M0-4 完成
- [x] 索引进度可视化（总进度、已完成/总数、预计剩余、当前文件与插件）—— M0-12 + SSE 完成
- [ ] 插件管理（启停/参数表单/定向重跑）→ M1-9
- [x] 失败重试（单资产/单插件粒度 + 错误摘要）—— M0-12 完成

### 系统与隐私

- [x] 只读挂载校验（启动校验，可选关闭）→ M0-3（已完成，详见 M0-3）
- [x] 健康检查 `/api/health` —— M0-8 完成
- [x] 单实例运行（v1 单用户，数据模型已预留）—— 现状即单用户

---

## v1 回收站（软删除 + 还原，默认保留 30 天）

> 2026-08-07 完成。

**目标**：用户误删可恢复，避免误操作造成永久数据丢失；同时尊重 M0 关于「媒体根只读」的不变量 —— 文件本身永远由文件系统管理，HomeTrove 只索引、不删除源文件。

**模型变更**

- `assets` 新增 `deleted_at: int | NULL`（epoch 秒）；NULL 表示在线，非 NULL 表示在回收站
- 配套索引 `idx_assets_deleted_at`
- alembic 迁移 `0004_asset_trash`（upgrade head → downgrade 0003 → upgrade head 已 round-trip 验证）

**默认过滤**

- `GET /api/assets` / `GET /api/assets/{id}` / `GET /api/assets/{id}/file` / `GET /api/assets/{id}/thumbnail` 默认隐藏 `deleted_at IS NOT NULL` 行；带 `?include_trashed=true` 可查看
- `GET /api/folders` / `GET /api/folders/assets?media_root=` 仅统计在线资产
- `GET /api/places` 通过 JOIN `assets` 自动排除
- `/api/search` 关键词命中但资产已软删 → 该 hit 在响应里被过滤

**API**

| 方法 | 路径 | 行为 |
|---|---|---|
| `POST` | `/api/assets/{id}/trash` | 软删除（幂等：已删资产保留原始 `deleted_at`） |
| `POST` | `/api/assets/{id}/restore` | 还原（幂等：在线资产返回 `deleted_at=null`） |
| `GET` | `/api/trash?limit=&offset=` | 按 `deleted_at DESC` 列出，支持分页；返回 `{items,total,limit,offset}` |
| `POST` | `/api/trash/empty?older_than_seconds=` | 永久清空；不带参数清空整个回收站，带参数只清空超过 N 秒的项 |

**关键不变量**

- 软删除只动 `assets.deleted_at` 与 `updated_at`；源文件零修改
- 永久清空走 FK `ON DELETE CASCADE`，级联删除 `plugin_results` / `embeddings` / `face_embeddings` / `asr_transcripts` / `album_assets` / `jobs`；**绝不触碰磁盘上的源文件**
- Worker 每 60 个 claim tick（默认 30s）跑一次 `purge_expired()`，但仅在 `HOMETROVE_TRASH_AUTO_PURGE=true` 时启用（默认关闭，避免首次部署误删）

**CLI**

```
hometrove trash prune [--older-than-days N] [--dry-run]   # 一次过期清理
hometrove trash empty                                     # 立即清空整回收站
```

**前端**

- 侧边栏新增「回收站」入口 → `/trash`（按删除时间倒序、缩略图网格、显示「X 天前移入」、一键还原/刷新/批量永久删除）
- 资源详情页右上角：在线显示「移到回收站」按钮（带确认）；在回收站中显示「从回收站还原」按钮
- 资源详情页会显示红色「回收站 · 移入时间」徽章

**测试**（12 个新增）

- 软删除隐藏资产、幂等保留原始时间戳、未知 ID 返回 404
- 列表按 `deleted_at DESC` 排序 + 分页
- `POST /api/trash/empty` 仅删 DB 行，不删文件
- `older_than_seconds` 仅清空超过截止的行
- `purge_expired` 读取 `HOMETROVE_TRASH_RETENTION_DAYS` 并按设置天数清理
- 软删除资产从 `/api/search` 召回中消失
- 软删除资产从 `/api/folders` 与 `/api/folders/assets` 计数中消失
- `hometrove trash prune --dry-run` 只数不删、`hometrove trash empty` 整批清空
- 总计：91 测试全绿（79 原 + 12 新）

**配置**

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `HOMETROVE_TRASH_RETENTION_DAYS` | `30` | 软删除资产自动清理的天数；`<=0` 关闭自动清理 |
| `HOMETROVE_TRASH_AUTO_PURGE` | `false` | worker 是否每 30s 自动调用 `purge_expired()`；生产建议 `true` |

---

## v1 批量操作 + 收藏（多选 / 收藏 / 添加到相册 / 批量软删）

> 2026-08-07 完成。

**目标**：时间轴/回收站支持「选择模式 → 批量动作」，让用户一次处理大量照片；收藏提供「★」单资产标记与「只看收藏」筛选。

**模型变更**

- `assets` 新增 `favorite: int`（0/1，默认 0；整数而非 boolean，方便 COUNT/SUM/索引复用现有工具）
- 配套索引 `idx_assets_favorite`
- alembic 迁移 `0005_favorites`（upgrade head → downgrade 0004 → upgrade head round-trip 验证）

**API —— 收藏（单资产）**

| 方法 | 路径 | 行为 |
|---|---|---|
| `POST` | `/api/assets/{id}/favorite` | toggle（0↔1），幂等 |
| `PUT` | `/api/assets/{id}/favorite` | 置为收藏（显式） |
| `DELETE` | `/api/assets/{id}/favorite` | 取消收藏（显式） |

- 三个动词对未知 ID 均返回 404
- `GET /api/assets?favorite=true|false` 按收藏状态过滤；Asset DTO 新增 `favorite: bool`
- `GET /api/assets/{id}` 详情同样暴露 `favorite`

**API —— 批量**

所有批量端点接受 body `{"asset_ids": [int, ...]}`，返回 `{ok, requested, affected, missing}`；`requested` = 去重后请求数，`affected` = 实际变更数，`missing` = 不存在的 ID 列表。**400** 当 `asset_ids` 超过 `_BULK_MAX=1000`（防客户端误洪泛）。

| 方法 | 路径 | 行为 |
|---|---|---|
| `POST` | `/api/bulk/assets/trash` | 批量软删除（幂等：已删资产保留原 `deleted_at`） |
| `POST` | `/api/bulk/assets/restore` | 批量还原 |
| `POST` | `/api/bulk/assets/favorite` | 批量收藏 |
| `POST` | `/api/bulk/assets/unfavorite` | 批量取消收藏 |
| `POST` | `/api/bulk/assets/add-to-album` | body 加 `album_id`；追加到相册，保留已有成员顺序 + 请求顺序；已在相册的跳过 |

> 路由前缀用 `/api/bulk/assets/*` 而非 `/api/assets/bulk/*`：后者会与 `/api/assets/{asset_id}/trash` 等路径参数路由冲突（`bulk` 被当 `asset_id` 解析），FastAPI 按声明顺序匹配先到先得。

**前端**

- 时间轴新增「选择」按钮 → 进入选择模式（缩略图左上角出现 ✓ 圈，点选高亮）；底部浮层 action bar：收藏 / 取消收藏 / 添加到相册…（弹出相册选择器）/ 移到回收站（confirm）
- 时间轴过滤器：全部 / ★ 收藏 / 未收藏（`?favorite=true|false`）
- 每张缩略图 hover 显示 ☆/★ 收藏按钮（乐观更新 + 回滚）
- 资源详情页新增「☆ 收藏 / ★ 已收藏」切换按钮
- 回收站页新增「选择」模式 → 底部浮层「还原」；支持批量还原
- `bulk_actions.tsx` 组件：`useSelection` hook（Set 存储）+ `BulkBar`（context=`library|trash`），动作完成后 invalidate `assets/trash/asset/albums/search/folders/places` 查询键，并 toast 显示 `affected`/`missing` 部分失败计数

**测试**（11 个新增，合计 102 全绿）

- favorite toggle / set / clear 幂等 + 404
- favorite 暴露在 DTO + `?favorite=true|false` 过滤
- bulk trash / restore / favorite / unfavorite / add-to-album 的 `affected`/`missing`/顺序
- bulk trash 幂等（重跑 `affected=0`）、部分失败（missing 上报）
- bulk add-to-album 顺序保留 + 跳过已有成员
- unknown album → 404
- `_BULK_MAX` 超限 → 400
- favorite 不影响 folders/places 计数
- 总计：102 测试全绿（91 原 + 11 新）

---

## v1 共享相册（公开分享链接，权限 + 过期）

> 2026-08-07 完成。

**目标**：让相册可以生成公开链接，未登录访客能浏览；所有者控制「是否允许查看原图」「是否允许下载原图」「有效期」，并可随时撤销链接。

**URL 与 Token 设计**

- 采用「表存唯一 opaque token」方案：URL 只含短 token（`/share/{token}`），权限/过期存数据库。
- 不采用 JWT/signed payload URL：虽然无状态，但 URL 长、 revocation 困难、改权限会改 URL。
- token 用 `secrets.token_urlsafe(32)` 生成；查表即可防篡改、支持多链接、即时撤销。

**模型变更**

- 新增 `album_shares` 表：`album_id`, `token` (unique), `allow_original` (int 0/1), `allow_download` (int 0/1), `expires_at`, `created_at`, `created_by`
- 索引 `idx_album_shares_album`, `idx_album_shares_token`
- `Album.shares` relationship，cascade delete，删除相册自动撤销所有链接
- alembic 迁移 `0006_shared_albums`

**API —— 所有者管理（需登录；v1 仍为 passthrough，但接口已挂 `current_principal` 方便 v1.1）**

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/albums/{album_id}/shares` | body `allow_original`, `allow_download`, `expires_at`；返回 `share_url="/share/{token}"` |
| `GET` | `/api/albums/{album_id}/shares` | 列出未过期链接 |
| `DELETE` | `/api/albums/{album_id}/shares/{token}` | 撤销指定链接（token 不属于该相册返回 404） |

**API —— 公开访问（无鉴权）**

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/public/albums/{token}` | 相册信息 + `asset_ids`（已按相册顺序，自动过滤软删除资产） |
| `GET` | `/api/public/thumbnails/{token}/{asset_id}/{size}` | 公开缩略图，非成员/已删返回 404 |
| `GET` | `/api/public/files/{token}/{asset_id}` | 原图；`allow_original=false` 返回 403；`allow_download=true` 带 `Content-Disposition: attachment` |

**前端**

- 新页面 `/share/:token`（`SharedAlbum`）：读取公开接口、展示相册标题与缩略图网格、点击打开只读灯箱；支持键盘左右/Esc；底部「查看原图/下载原图」按钮仅在 `allow_original=true` 时出现。
- 相册详情页新增「分享」按钮，弹出 `ShareModal`：
  - 复选框「允许查看原图」「允许下载原图」（后者依赖前者）
  - 有效期下拉：永不过期 / 1 天 / 7 天 / 30 天
  - 已生效链接列表：显示 token、权限、过期时间、「复制链接」「撤销」

**测试**（7 个新增，合计 109 全绿）

- 创建链接并公开访问相册元数据与资产顺序
- 撤销链接后公开端点 404；跨相册撤销也 404
- 软删除资产自动从分享页消失
- `allow_original`/`allow_download` 对原图端点的权限控制（403/200/attachment）
- 非成员资产缩略图 404
- 过期 token 阻塞所有公开端点（`_live_share` 用 `<= now` 严格判断）
- 删除相册级联删除 share token

---

## v1 justified 网格 + 虚拟滚动

> 2026-08-07 完成。

**目标**：把固定 4:3 缩略图网格改成按原始宽高比紧密排列的 justified 布局，并只渲染视口附近的行，让大库也能流畅滚动。

**实现**

- 新增通用组件 `web/src/components/justified_grid.tsx`
  - `buildLayout(assets, containerWidth, targetRowHeight, gap)`：贪心装箱，每行保持统一高度并拉伸至容器宽度；未知宽高时默认 4:3
  - `JustifiedGrid`：监听 `ResizeObserver` 与容器 `scroll`，计算总高度、可见行切片（`overscan=3`），仅挂载可视行 + 上下缓冲行
  - 每个 cell 通过绝对定位 `left/top/width/height` 渲染，不依赖 CSS grid 的固定比例
- 不引入新运行时依赖，纯 React hooks + DOM API

**接入页面**

- `Timeline`：整体改为 `flex h-full flex-col`，顶部过滤器固定，下方 `JustifiedGrid` 占满剩余高度并自行滚动；保留选择模式、收藏 star、批量操作栏、灯箱预览、无限滚动加载下一页
- `AlbumDetail`：相册内照片改用 `JustifiedGrid`，hover 仍显示「设为封面 / 移出相册」按钮
- `SharedAlbum`：公开相册同样接入 `JustifiedGrid`，灯箱点击打开

**测试**

- 后端 API 行为无变化，全量 109 测试保持绿色
- 前端 `npm run build` 通过

---

## v1 智能相册

> 2026-08-07 完成。

**目标**：支持按人脸/地点/标签/分类/时间/媒体类型/收藏状态组合条件，创建自动更新的智能相册。v1 只提供简单 JSON 规则 DSL 和基础表单编辑器，不做可视化拖拽规则构建器。

**实现**

- 数据模型
  - `albums.is_smart` 标记智能相册
  - 新增 `smart_album_rules` 表，与 `albums` 一对一级联删除
- 规则 DSL（最多嵌套 3 层）
  - 组合算子：`and` / `or`
  - 叶子算子：`person` / `place` / `tag` / `category` / `time` / `media_type` / `favorite`
- 后端
  - `hometrove/smart_albums.py`：规则校验 `validate_rule()` + 评估 `eval_rule()`
  - 扩展 `/api/albums`：创建/更新支持 `is_smart` + `rule`；列表/详情对智能相册返回实时计算的 `asset_ids` 与 `rule`
  - 手动相册的添加/移除/排序接口对智能相册返回 400
  - 共享相册 public 端点兼容智能相册（按规则计算成员）
- 前端
  - 相册列表卡片显示「智能」badge
  - 新建相册支持切换「智能相册」并编辑规则
  - 智能相册详情隐藏「添加照片/设为封面/移出相册」，显示「编辑规则」按钮
  - `RuleEditor` 组件支持嵌套 and/or 组与叶子条件下拉选择

**测试**

- 新增 5 个测试：CRUD + 规则求值、person + favorite、无效规则、排除已删除、place 规则
- 全量 114 测试通过
- 前端 `npm run build` 通过

---

## v1 地点地图（Leaflet + OpenStreetMap 交互式地图视图）

> 2026-08-07 完成。

**目标**：把 M1-8 的静态等距圆柱投影散点地图升级为可缩放/平移的交互式地图，点选聚类圆点查看该地照片。

**实现**

- 新增依赖 `leaflet@1.9.x` + `react-leaflet@5.0.x`（兼容 React 19）+ `@types/leaflet`（dev）
- `web/src/routes/places.tsx` 重写：
  - `MapContainer` + `TileLayer` 加载 OpenStreetMap 瓦片，保留 OSM attribution
  - `CircleMarker` 渲染聚类，半径与 `count` 成正比，选中高亮；`Tooltip` 常显数量
  - `FitBounds` 子组件在聚类变化时自动 `fitBounds` 适应全部坐标
  - 保留「全部聚类」chip 列表与资产缩略图网格，选中态与地图标记联动
  - 新增错误态（加载失败 + 重试）与无 GPS 空态降级
- 路由懒加载：`App.tsx` 用 `React.lazy` 加载地点页，Leaflet（约 163KB 独立 chunk）不进入主包，主包仍约 366KB

**测试**

- 后端 `/api/places` 无改动，既有聚类测试保持覆盖；全量 114 测试通过
- 前端 `npm run build` 通过

---

## v1 高级筛选（组合条件过滤 + 日期/地点后端支持）

> 2026-08-07 完成。

**目标**：时间轴页提供可展开的组合筛选面板，支持媒体类型、日期范围、人物、地点、标签、收藏状态叠加过滤，结果沿用 justified 网格 + 无限滚动。

**实现**

- 后端 `/api/assets` 新增参数（`hometrove/api/routes/assets.py`）
  - `taken_after` / `taken_before`（epoch 秒，`>=` / `<` 边界，非法负值 422）
  - `place`（聚类网格中心 `"lat,lon"`，pattern 校验，非法格式 422），复用 `smart_albums._place_ids` 判定网格单元
  - 所有条件与既有 `media_type`/`favorite`/`tag`/`category`/`person_id` 取 AND 交叠
- 前端
  - `web/src/lib/api.ts`：`assets` 签名改为结构化 `AssetsFilters`，新增 `toggleFavorite`/`moveToTrash`/`restoreFromTrash`/`trash`/`emptyTrash`/`bulkTrash`/`bulkRestore`/`bulkFavorite`/`bulkAddToAlbum`，补齐 `AssetDTO.favorite`/`deleted_at`
  - `web/src/routes/timeline.tsx`：新增 `FilterPanel`（媒体类型/收藏/日期起止/人物/地点/标签下拉，active 条件计数，一键清除全部），`queryKey` 绑定完整筛选条件
  - 修复前序 v1 功能因 esbuild 不查类型而长期静默的前端类型错误（`bulk_actions`、`asset_detail`、`trash`、`places`、`albums`、`shared_album`、`kv`）——`npx tsc --noEmit` 从多报错归零

**测试**

- 新增 3 个测试：日期范围边界（`>=` / `<`）、place 网格过滤 + 非法格式 422、多条件组合（media_type+tag+person_id+favorite+日期）
- 全量 117 测试通过（114 → 117）
- 前端 `npx tsc --noEmit` 零错误，`npm run build` 通过

---

## v1 以图搜图（向量近邻召回）

> 2026-08-07 完成。

**目标**：从任意资产出发，召回视觉上最相似的资产，复用既有 `embedding.jina_clip` 向量管道（`embeddings` 表 + `embedding_vec` sqlite-vec 索引），不引入新依赖。

**规格文档**：`.monkeycode/specs/v1-image-similarity/requirements.md` + `design.md`

**后端**

- `hometrove/search.py` 新增 `similar_assets(session, asset_id, k)`：
  - 目标资产向量选择优先级：`image` scope 优先，其次任意 `scene`（`case` 排序 image first + `Embedding.id`）
  - `get_index().search(vec, k)` 近邻召回 → 排除目标资产自身与软删除资产 → 同一资产去重后取首个近邻，距离升序
  - 目标无向量或索引缺失：返回空列表（HTTP 200），不报错
- `hometrove/api/routes/assets.py` 新增 `GET /api/assets/{asset_id}/similar?limit=`（默认 24，ge=1 le=100）
  - 目标不存在或已软删除 → 404
  - 每个命中返回 `asset_id` / `media_type` / `duration_sec` / `distance` / `scope` / `t_start` / `t_end`

**前端**

- `web/src/lib/api.ts`：新增 `SimilarHitDTO` + `api.similar(id, limit=24)`
- `web/src/routes/asset_detail.tsx`：非回收站状态新增「找相似」区块（最近邻召回），结果网格复用 `search.tsx` 缩略图样式（`SimilarCard`：缩略图 + 视频占位 + 距离百分比角标），点击跳转 `/asset/{id}`；加载中/错误重试/空态三种状态

**测试**

- 新增 4 个测试：近邻排序 + 排除自身、无向量空列表、未知/软删除目标 404、排除软删除近邻
- 全量 121 测试通过（117 → 121）
- 前端 `npx tsc --noEmit` 零错误，`npm run build` 通过

---

## v1.1 建议

- [ ] 多用户与基础权限（「多用户+各自私有库」或「单库+角色」，**待决策见 §待决策 14.3**）
- [ ] 共享链接 + 公开相册
- [ ] ASR 语音转写（faster-whisper）→ M1-10（v1 内已交付骨架，详见 M1-10）
- [ ] Live Photo 完整支持（motion photo + HEIC 联动）
- [ ] RAW 缩略图（dcraw / libraw）
- [ ] 地图聚合增强（按城市/国家聚合、Cluster）
- [ ] 智能相册可视化条件规则编辑器
- [ ] 视频 GPU 转码（ffmpeg nvenc / qsv / videotoolbox）
- [ ] 移动端 PWA / 原生应用评估与原型
- [ ] 只读校验升级为强制（可在高级设置豁免）

## v2 远期

- [ ] 跨语言描述（多语种 VLM 输出）
- [ ] 物体检测框（bounding box 可视化与点击筛选）
- [ ] 视频目标跟踪一致性（跨镜头同人识别）
- [ ] 时间线事件自动成片（人脸聚类 + 时间窗口 + 配乐）
- [ ] 跨库联邦搜索（家庭成员各自部署后互搜）
- [ ] 自然语言对话式检索（Agent 风格多轮精炼）
- [ ] 移动端原生应用（自动备份 / 推送）

---

## 明确不做（排除项）

| 项 | 排除理由 |
|---|---|
| 社交动态 / 评论 / 点赞 | 与「家庭影像管理」定位不符，膨胀数据模型与审核责任 |
| 云端同步 | 与隐私立场、部署形态冲突 |
| 付费订阅 / 内购 | 开源项目，与 AGPL 一致 |
| 编辑器（裁剪/调色/滤镜） | 与 darktable / Lightroom 强重叠，价值有限 |
| 跨设备实时协作 | 单用户主场景下不构成痛点 |
| 公有云后端适配（S3 / OSS 替代 NAS） | 数据主权边界模糊；用户可用 rclone / 同步盘自行解决 |

---

## 待决策事项

> 这些决策影响架构边界，README 阶段不替用户拍板。决定后在此打勾并记录结论。

- [ ] **VLM 默认运行时**：A 内置 llama.cpp / B 用户自备 Ollama / vLLM / C 双模式（检测到 GPU 用 vLLM 否则 llama.cpp）。倾向 C，待观察家用 NAS 是否真有独显
- [ ] **v1 范围裁剪**：以下项可从 v1 推迟到 v1.1——ASR（模型大、要求 GPU）、地点地图（聚类 + Cluster 约 1~2 周）、共享链接/公开相册（token/鉴权/限流）
- [ ] **权限模型粒度**：A 多用户+各自私有库（隔离强、成本高）/ B 单库+角色（共享、ACL 控制）。必须在 v1.1 启动前定
- [ ] **调研分歧点**（实施期逐项确认）：
  1. Live Photo 存储形态（HEIC+.mov 是否要求紧邻命名，沿用 PhotoPrism 或 Immich 策略）
  2. HEVC / HEIC 支持（是否 v1 就转码，还是仅浏览器能力 + 后端 JPEG 兜底）
  3. 视频转码策略（统一 MP4 vs 仅 HLS，对 CPU/存储影响大）
  4. 家目录与共享空间的物理路径映射（v1 单空间 or 直接双空间）
  5. 重复检测/相似项（倾向：视觉相似做在「以图搜图」路径，精确重复作清理工具）
  6. EXIF 写入（反写 XMP 与只读立场冲突，倾向 v1 不做、v2 评估）
  7. NER / 时间戳解析（倾向 v1 只识别时间量词，其他由 VLM 与 FTS 兜底）

---

## 当前进行中

- 最后一勾：**M1 插件扩展**。本线已完成 M1-1~M1-8、M1-5 前置基础设施（`PluginContext` 共享缓存 + `resolve_asset_path`）、**M1-9（完整）**（插件开关 + 参数表单 + 定向重跑）、**M1-10 asr + 上传预设**（`asr.faster_whisper` 抽音频 + 双后端 + `asr_transcripts` 表 + 详情页/搜索暴露，alembic 0003）、**M1-11 鉴权骨架**（`AuthBackend` 协议 + Passthrough 默认实现 + 中间件 + FastAPI 依赖项 + 配置开关）。**M1-7 已实现双路召回 + RRF 融合 + 视频跳秒**（mock 向量阶段，关键词路径已纳入 audio 字幕召回）。70 测试全绿。M1 单线收尾；M1-5 `vlm.qwen3vl` 仍在并行任务线。
