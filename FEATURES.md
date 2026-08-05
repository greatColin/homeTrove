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
| M0-3 | 媒体根只读校验（启动时 warning，v1.1 升级强制） | [ ] | 手动 `chmod -w` 后启动日志有明确提示 |
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
| M1-10 | `asr.faster_whisper` 语音转写 | [ ] | M1-3 | 抽取音频 → 带时间戳字幕；scope=`audio`（v1.1 亦可） |
| M1-11 | 鉴权骨架接入 | [ ] | — | `AuthBackend` 进中间件，默认放行；为多用户预留 |

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
- [ ] 网格布局 justified（等高两端对齐）+ 视频缩略图代表帧
- [ ] 虚拟滚动（10 万张无明显抖动）
- [x] 灯箱（大图浏览、键盘导航）—— 基础版已实现（timeline Lightbox）
- [ ] 批量操作（多选 / 移动 / 收藏 / 添加到相册 / 软删除）
- [ ] 收藏（单资产标记）
- [ ] 回收站（软删除 + 还原，默认保留 30 天）

### 组织

- [ ] 手动相册（创建/重命名/排序/封面/描述）
- [ ] 共享相册（v1 仅数据模型层，不做外部访问控制）
- [ ] 智能相册（v1 仅基于人脸/地点/标签的简单条件筛选，无可视化规则编辑器）
- [x] 标签（自动 + 手动）—— 自动标签由 M1-5 的 VLM + M1-1 物体识别提供；页面已就绪
- [x] 人物（人脸聚类）—— **核心已完成**：命名/合并/过滤/JSON 信息（`/people` 页）；HDBSCAN 聚类算法待接入
- [ ] 地点（GPS 聚合 + Leaflet + OpenStreetMap 地图视图）

### 检索

- [ ] 高级筛选（媒体类型/日期范围/人物/地点/标签/相机/镜头）
- [ ] 语义搜索 `/search`（双路 + RRF，视频片段跳秒）→ 即 M1-7
- [ ] 以图搜图（v1 简化：上传图或从资产出发召回相似项）

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

- [ ] 只读挂载校验（启动校验，可选关闭）→ M0-3（未做）
- [x] 健康检查 `/api/health` —— M0-8 完成
- [x] 单实例运行（v1 单用户，数据模型已预留）—— 现状即单用户

---

## v1.1 建议

- [ ] 多用户与基础权限（「多用户+各自私有库」或「单库+角色」，**待决策见 §待决策 14.3**）
- [ ] 共享链接 + 公开相册
- [ ] ASR 语音转写（faster-whisper）→ M1-10
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

- 最后一勾：**M1 插件扩展**。本线已完成 M1-1~M1-8、M1-5 前置基础设施（`PluginContext` 共享缓存 + `resolve_asset_path`）与 **M1-9（完整）**（插件开关 + 参数表单 + 定向重跑，55 测试全绿）。**M1-7 已实现双路召回 + RRF 融合 + 视频跳秒**（mock 向量阶段）。剩余项：M1-10 `asr.faster_whisper`、M1-11 鉴权骨架。注：M1-5 `vlm.qwen3vl` 已另起任务并行开发，不在此进度单内推进。
