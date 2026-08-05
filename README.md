# HomeTrove ｜ 家藏

> **Not just stored. Understood.**
> 不只是存下来，而是被读懂。

[![License](https://img.shields.io/badge/license-AGPL--3.0-blue)](LICENSE)
[![Status](https://img.shields.io/badge/status-pre--alpha-lightgrey)]()
[![Stack](https://img.shields.io/badge/stack-Python%203.11%20%2B%20React%2019-green)]()

HomeTrove（家藏）是一个跑在你自己 NAS 上的家庭影像（照片 + 视频）管理系统。它不只是把照片和视频存起来、按时间排好，而是用 AI **真正读懂** 每一张图和每一段视频的内容，让你可以用一句人话把它们找出来。

---

## 目录

1. [为什么做这个](#1-为什么做这个)
2. [核心特性](#2-核心特性)
3. [当前进度](#3-当前进度)
4. [与同类项目对比](#4-与同类项目对比)
5. [页面与交互设计](#5-页面与交互设计)
6. [技术架构](#6-技术架构)
7. [技术选型](#7-技术选型)
8. [插件系统](#8-插件系统)
9. [索引进度与估算](#9-索引进度与估算)
10. [数据模型概览](#10-数据模型概览)
11. [部署形态](#11-部署形态)
12. [待决策事项](#12-待决策事项)
13. [参与贡献](#13-参与贡献)
14. [License](#14-license)

---

## 1. 为什么做这个

家里有十万张照片和几千段视频。**记得拍过某个画面，但永远找不到它**——这是几乎每个家庭影像积累到一定体量后都会撞到的墙。

现有的自托管相册（Immich、PhotoPrism）和云相册（Google Photos、Apple Photos）面对这个问题时，给出的能力上限是：

- 按 **时间** 翻
- 按 **文件夹** 翻
- 按 **人脸** 翻
- 最多按 **物体 / 场景标签** 翻

而视频——家庭媒资中**体量最大、却最没被认真对待的部分**——在所有这些工具里几乎都被简化为「一张缩略图 + 一个文件」。你**搜得到**这部视频，却永远**回不到**里面那个具体的镜头。

HomeTrove 想做的是一件很简单的事：

> 让一段视频成为**可以被逐秒检索的内容**，而不是一个可播放的文件名。

具体来说：

- 你输入「孩子在海边踩浪花的背影」，系统返回 5 张图和 2 个**视频片段**。点开视频，**播放器直接跳到那个镜头开始的那一秒**。
- 你输入「去年春节老爸打麻将的侧脸」，系统把人脸、场景、时间三个线索合并，给出命中。
- 系统不只读图，也**读视频里的每一帧**：场景切分、镜头内人脸、关键帧描述、可选的语音转写。

并且，这一切推理**完全在本地**完成——无云端、无上传、无订阅，原始媒体目录默认**只读挂载**，程序不具备删改原文件的能力。

---

## 2. 核心特性

### 2.1 视频和图片同等被理解

- **图片**：内容识别、中文描述生成、向量召回、FTS5 召回、人脸识别、地点聚类、自动标签。
- **视频**：先做场景切分（PySceneDetect ContentDetector），每个场景独立抽取关键帧，每个关键帧独立建索引——一段 30 分钟的家庭视频可能产生 40~80 个**检索单元**，而不是 1 个。

### 2.2 双路语义检索

任何对语义检索的纯向量方案都有一个共同的软肋：**专有名词**（人名、地名、品牌）几乎不可能从图像向量里召回，因为 CLIP 家族训练数据里几乎没有这些实体。

HomeTrove 采用两条召回路径，**RRF 融合**：

- **路径 A（视觉）**：jina-clip-v2 直接编码图像/关键帧 → 擅长视觉相似与简单描述（「海边」「日落」）。
- **路径 B（语义）**：Qwen3-VL-4B 生成中文描述 → bge-m3 编码 → 擅长复杂组合（「爸爸和姥爷在老家院子里下棋」「川菜馆里一桌人举杯」）。

### 2.3 检索命中后播放器直接跳秒

视频被建模成「场景 → 关键帧」的多级结构。`embeddings` 表存的是 `(asset_id, scope, t_start, t_end, vec)`，`scope` 区分 `image` / `scene` / `audio`（v1.1）。命中后播放器读取 `t_start`，从该秒开始播放。

### 2.4 自然语言作为一级入口

`/search` 与时间轴、相册、人物、标签、地点 **平级** 的一级页面，不是藏在高级筛选里的子功能。

### 2.5 可插拔文件分析体系

视频解码、抽帧、人脸、VLM、向量、ASR 全部走同一套插件接口。每个插件声明依赖、执行耗时估算、设备类型，前后端借此**自动渲染配置表单**。详细规范见 [§8 插件系统](#8-插件系统)。

### 2.6 隐私与只读

- 原始媒体目录强制 `read-only` 挂载，应用**没有**能力删改原文件。
- 所有 VLM / 人脸 / 向量推理在本地。
- 无外发网络请求（除非用户主动开启远程模型端点）。

---

## 3. 当前进度

> 本节是进度快照；每项的**验收口径与依赖关系**以 **[`FEATURES.md`](FEATURES.md)** 为唯一权威。

**阶段总览：**

| 阶段 | 主题 | 状态 |
|---|---|---|
| **M0** | 基础框架：扫描 → 入库 → 浏览/上传闭环 | ✅ 完成（2026-08-04），13/14 项 |
| **M1** | 插件扩展：真实内容识别 + 检索 + 分类页 + 插件管理 | ⏳ 进行中 |
| **v1 必备** | M1 完成后的产品收尾 | ⬜ 未开始 |
| **v1.1** | 多用户 / 共享 / ASR / 移动端等 | ⬜ 未开始 |
| **v2** | 远期能力 | ⬜ 未开始 |

### M0 基础框架（✅ 已完成，2026-08-04）

- [x] M0-1 仓库骨架（`hometrove/` + `web/` + `pyproject.toml`）
- [x] M0-2 SQLite(WAL) + SQLAlchemy 2.0 + Alembic（`assets/plugin_results/jobs/plugin_config`）
- [ ] M0-3 媒体根只读校验 —— 推迟至 M1
- [x] M0-4 扫描器：初始全量 + 轮询增量 + `content_hash` 去重
- [x] M0-5 `basic.info` 插件（name/media_type/size/mtime/hash + 尽力读宽高/时长/拍摄时间）
- [x] M0-6 Worker + DAG 拓扑调度（`depends_on`）
- [x] M0-7 SSE 进度推送（worker → api → 前端）
- [x] M0-8 REST API：`/api/assets` `/api/folders` `/api/health` `/api/jobs`
- [x] M0-9 前端骨架（React 19 + Vite + Tailwind + TanStack Query）
- [x] M0-10 `/timeline` 时间轴（M0 用占位色块，无实缩略图）
- [x] M0-11 `/folders` 原始目录树
- [x] M0-12 `/settings/jobs`（进度 + 队列 + 失败重试）
- [x] M0-13 纯 Python 单进程 `hometrove serve`
- [x] M0-14 文档 `docs/INSTALL.md` + `docs/DEV.md`

> **M0 额外交付**：分片上传/断点续传/幂等重传、`enqueue_pending` 幂等入队、任务中心按文件聚合、3 个模拟插件（`mock.tags/category/faces`）、facets 聚合与过滤、文件详情页 `/asset/:id`、**人脸分组系统**（embedding + `face.match` 归组 + 人员管理 API 与页面，即 M1-4 提前落地）。

### M1 插件扩展（⏳ 进行中，按序号推进）

- [x] M1-1 `thumbnail` 缩略图插件（纯 Python：Pillow 多档位 + PyAV 视频抽帧真帧，网格真实缩略图）
- [x] M1-2 `exif` 插件（纯 Python：Pillow EXIF + PyAV 视频元数据，相机/镜头/ISO/曝光/GPS；已替换 exiftool）
- [x] M1-3 `basic.scene_detect` 视频场景切分（PySceneDetect + pyav 后端，输出场景秒范围 + keyframe）
- [x] M1-4 `face.detect` 真实人脸（InsightFace SCRFD+ArcFace 512 维，CPU onnxruntime；图片全帧 + 视频 keyframe 抽帧去重）
- [ ] M1-5 `vlm.qwen3vl` 中文描述（前置已就位：`PluginContext.image()/frames()/result_of()` 共享缓存 + `resolve_asset_path`）
- [x] M1-6 `embedding.jina_clip` + `embedding.bge_m3`（sqlite-vec 接入，`embeddings` 表 image/scene scope；**当前 mock 向量阶段**，caption scope 待 M1-5）
- [x] M1-7 `/search` 语义搜索（双路召回 + RRF，视频跳秒；**当前 mock 向量阶段**）
- [x] M1-8 `/albums` `/tags` `/places` 分类页（`/api/albums` CRUD + 资产添加/移除、`/api/places` exif GPS 网格聚类；前端 `/albums` `/places` 页；`/tags` `/categories` 模拟页、`/people` 已实现）
- [x] M1-9 `/settings/plugins` 插件管理（**完整**：开关 + `params` 参数表单（按 `params_schema` 自动渲染、保存时 `ParamsModel` 校验）+ 版本展示 + 定向重跑 `POST /api/plugins/{id}/rerun`；禁用插件不入队/停驻、重启用恢复、禁用时 `shutdown()` 释放内存）
- [ ] M1-10 `asr.faster_whisper` 语音转写（asr 本身待开发）；**上传插件预设**（`plugin_presets` 表含内置默认/会议/旅游+用户自定义 CRUD；`/api/upload-presets` GET/POST/DELETE；上传页选预设 → 插件 checkbox 列表回填 → 手动调整 → `POST /api/uploads/{id}/ingest?plugin_ids=` 入队；57 测试全绿）
- [ ] M1-11 鉴权骨架（默认放行，为多用户预留）

### v1 必备（M0/M1 已覆盖的项）

- 已覆盖：时间轴基础版、文件夹视图、灯箱、标签页（自动标签待 M1-5）、人物核心（命名/合并/过滤）、索引扫描、索引进度可视化、失败重试、健康检查、单用户
- 待补齐（M1 完成后）：justified 网格、虚拟滚动、批量操作、收藏、回收站、相册、共享相册、智能相册、地点地图、高级筛选、语义搜索、以图搜图、场景切分、关键帧、静音预览、跳秒播放、插件管理

### v1.1 / v2

未开始。多用户、共享链接、ASR、Live Photo、RAW、GPU 转码、移动端等见 [FEATURES.md](FEATURES.md#v11-建议) 与 [FEATURES.md](FEATURES.md#v2-远期)。

**下一项：M1-10（上传插件预设）已完成并推送（57 测试全绿）；asr 本体待开发；M1-11 鉴权骨架待推进（或用户指定顺序）。M1-5 `vlm.qwen3vl` 另起任务并行开发。**

---

## 4. 与同类项目对比

> 对比基线：以 **2026 年 8 月** 公开可查的官方文档与社区资料为准，后续功能变化以最新版本为准。

| 维度 | HomeTrove | Synology Photos | Immich | PhotoPrism | Google Photos |
|---|---|---|---|---|---|
| 部署形态 | 自托管 NAS | 群晖 NAS 商业套件 | 自托管 | 自托管 | 云端 SaaS |
| 核心识别 AI | Qwen3-VL-4B + jina-clip-v2 + InsightFace | Synology 自研（NAS 本地推理，依赖机型） | OpenCLIP + InsightFace + ML 容器 | TensorFlow（NASNet 系） | Google Gemini |
| 语义检索范围 | 图片 + 视频（按场景逐秒） | 图片为主（视频仅元数据级） | 图片为主（视频取首帧作为缩略图） | 图片为主（视频仅元数据级） | 图片 + 部分视频 |
| 视频是否被真正理解 | **是**：场景切分 + 关键帧检索 + 命中跳秒 | 否（仅整段打标签） | 否（首帧向量代表整段） | 否（仅元数据 + 标签） | 部分（部分镜头可被搜索） |
| 视频场景级检索命中跳秒 | **是** | 否 | 否 | 否 | 弱支持 |
| 中文理解能力 | **强**：VLM 原生中文 + bge-m3 | 中（依赖训练数据） | 中（CLIP 中文弱） | 弱（早期模型对中文不友好） | 强 |
| 视频人脸 | 是（关键帧检测 + 跟踪去重） | 否（仅图片人脸） | 是（视频抽帧，无轨迹去重） | 仅图片 | 是 |
| ASR（语音转写） | v1.1（faster-whisper 插件） | 否 | v1.x 实验性 | 否 | 否（仅自家 Pixel） |
| 插件化分析 | **是**（DAG + Context 缓存 + 版本化重跑） | 否 | 否（固定 ML 流水线） | 否（固定 TF 模型） | 否 |
| 数据权限模型 | v1 单库；多用户预留 | 多用户 + 个人空间 + 共享空间（细粒度权限） | 多用户 + 共享相册 + 公开链接 | 多用户 + 公开链接 + API | 云账户 + 共享链接 |
| 移动端应用 | v1 仅 Web（v1.1+ 评估） | iOS / Android / Apple TV / Android TV | iOS / Android 原生 | PWA | iOS / Android / Web |
| 视频转码 | v1 依赖浏览器原生（可选用 ffmpeg） | Synology Video Station 集成 | HLS + 实时转码 | 基础 | 云端透明转码 |
| 部署底座 | 单 SQLite 文件 + Docker | DSM 套件 | Postgres + Redis + ML 容器 + 至少 6 GB RAM | Go 服务 + SQLite / MariaDB | 不可自托管 |
| 隐私姿态 | 完全本地，永不上传 | 本地（除分享链接） | 本地（除分享链接） | 本地（除分享链接） | 数据归 Google |

### 4.1 关键差异解读

- **「视频被真正理解」不是炫技**：它是**唯一能解决「记得拍过某个画面」这一痛点**的路径。以视频首帧做代表会在镜头切换场景里完全失效。
- **「双路召回 + RRF」是质量分水岭**：Immich 的 CLIP 单路方案对中文专有名词（地名、人名、品牌、菜名）召回率明显衰减；PhotoPrism 的 TF 标签体系则更接近关键词检索。多模态 VLM 描述 + 文本向量器是 2024 年之后社区公认的更优解。
- **「插件化」是社区可扩展的杠杆**：PhotoPrism 之所以长期缺中文模型，根本原因是 TF 模型被硬编码到主进程。HomeTrove 通过插件接口和 `params_model` 把这件事变成了可由第三方 PR 单独迭代的子项目。

---

## 5. 页面与交互设计

### 5.1 页面清单

| 路由 | 用途 | 主要功能 | 当前状态 |
|---|---|---|---|
| `/timeline` | 时间轴（默认首页） | 按日/月/年缩放、右侧刻度、虚拟滚动、过滤栏 | ✅ 基础版 |
| `/search` | **语义搜索** | 自然语言输入、结果栅格、视频片段+跳秒播放、最近搜索 | ✅ M1-7（mock 向量阶段） |
| `/albums` | 相册 | 列表 + 详情；手动创建、相册内浏览 | ✅ M1-8 |
| `/people` | 人物 | 聚类后的人脸 cover、命名、合并、隐藏、忽略 | ✅（前端 `/faces`） |
| `/tags` | 标签 | 全部标签云、按标签筛选 | ✅（模拟数据） |
| `/places` | 地点 | 地图视图 + GPS 聚类列表 | ✅ M1-8 |
| `/folders` | 原始目录树 | NAS 用户保留入口 | ✅ |
| `/settings/plugins` | **插件管理** | 开关、参数表单（由 `params_model` 自动渲染）、版本、定向重跑 | ✅ M1-9 |
| `/settings/jobs` | **索引进度** | 总进度、队列、当前任务、失败重试 | ✅（实际路由 `/jobs`） |
| `/settings/library` | 媒体库 | 目录挂载、扫描触发、只读校验、回收站 | ⬜ |
| `/settings/account` | 账号（v1 占位） | 单用户设置、密码修改（v1.1 启用） | ⬜ |

### 5.2 设计基调（执行层约定）

> 此节是给前端实现的可执行规范，不是营销话语。

**视觉语言**

- **配色**：浅色 / 深色双模，跟随系统；强调色低饱和（默认 #5B7CFA 蓝色调），避免视觉噪声；主操作按钮与选中态使用同一色相的不同明度。
- **留白**：卡片之间 4~8 px，区块之间 16~24 px；少描边，多用阴影 + 圆角（12~16 px）分层。
- **图标**：使用 lucide-react，线性风格，1.5 px 描边；避免拟物化。
- **字体**：默认系统字体（`font-family: ui-sans-serif, system-ui`）；CJK fallback 至 `PingFang SC / Microsoft YaHei / Noto Sans CJK SC`。

**导航结构**

- **桌面端**：左侧主导航 240 px 固定栏（一级页面进入点），顶栏右侧放搜索入口与账号菜单；照片详情打开时右侧推出 360 px 信息栏（Info Panel），不抢主网格。
- **平板**：左侧栏折叠为抽屉。
- **手机**：左侧栏改为底部 Tab Bar 5 项（时间轴 / 搜索 / 相册 / 我的 / 设置）。

**照片网格**

- 使用 **justified 网格（等高两端对齐）**，而非固定方格：行高 220 px（桌面）/ 140 px（移动），让横竖比例不同的照片都能形成饱满的视觉块。
- 视频缩略图右下角显示时长角标。
- 选中态：边框 2 px 强调色 + 浅色蒙层；多选用 `<input type=checkbox>` + 视觉替换，长按进入批量模式（移动端）。

**右侧 Info Panel**

- 内容：文件名、拍摄时间、媒体类型 / 时长、相机机身与镜头、镜头参数（ISO / 光圈 / 快门）、GPS、所在文件夹、已加入的相册、已标记的人物、已绑定的标签、来源插件与版本。
- 折叠：仅显示高置信度信息（>= 阈值），其余折叠到「显示全部」。

**加载与空态**

- 缩略图加载：先 LQIP（4~8 px 模糊预览）再高清。
- 空态：插画 + 一句话引导，不放「重试」按钮式骚扰。

**键盘与快捷键**

- `←/→` 灯箱前后；`F` 收藏；`L` 加入相册；`Delete` 移到回收站；`/` 聚焦搜索框；`Esc` 关闭浮层。

**通用约定（与 Google Photos / Immich 对齐）**

- 时间轴「按日」「按月」「按年」双指捏合 / 顶部切换按钮。
- 缩放级别跨级别保留当前位置（吸附到最近的时间桶）。
- 共享操作隐藏到二级菜单，避免误触。
- 所有破坏性操作（删除、移除）需要二次确认，并明确「不会修改原始文件」。

### 5.3 移动端差异

v1 仅 Web，PWA 形式可安装。后续若做原生应用，关键差异：

- 原生应用负责**自动备份**与**推送**（v2）。
- Web 端负责**管理与索引**。
- 共享相册查看可双端。
- 移动端批量选择改用长按进入。

---

## 6. 技术架构

### 6.1 一图总览

```
                                   ┌───────────────────────────────┐
                                   │         Browser (Web)          │
                                   │  React 19 + TS + Vite +        │
                                   │  Tailwind + shadcn/ui         │
                                   │  TanStack Query / SSE          │
                                   └───────────────┬───────────────┘
                                                   │  REST + SSE
                                                   ▼
                              ┌────────────────────────────────────────┐
                              │           API Server (FastAPI)         │
                              │  ┌────────────┐  ┌─────────────────┐   │
                              │  │  Routers   │  │  Plugin Hosts   │   │
                              │  └─────┬──────┘  └────────┬────────┘   │
                              │        │                  │            │
                              │  ┌─────▼──────────────────▼─────────┐  │
                              │  │     Plugin Orchestrator (DAG)    │  │
                              │  │  estimate() → schedule → run()   │  │
                              │  └────────────────┬─────────────────┘  │
                              │                   │                    │
                              │  ┌────────────────▼─────────────────┐  │
                              │  │   Domain Services (assets,        │  │
                              │  │   search, faces, albums, jobs…)  │  │
                              │  └─┬─────────┬──────────┬───────────┘  │
                              └────┼─────────┼──────────┼──────────────┘
                          reads    │         │ writes   │ enqueues
                                   │         ▼          ▼
                          ┌────────▼────┐  ┌──────────────────────────┐
                          │ Media FS   │  │      SQLite (WAL)        │
                          │ (read-only)│  │  ┌──────────────────┐    │
                          │  /photos   │  │  │ assets, faces,    │   │
                          │  /videos   │  │  │ tags, persons,    │   │
                          └────────────┘  │  │ plugin_results,  │   │
                                          │  │ plugin_config,   │   │
                                          │  │ jobs, albums     │   │
                                          │  ├──────────────────┤   │
                                          │  │ FTS5 (text)      │   │
                                          │  ├──────────────────┤   │
                                          │  │ sqlite-vec       │   │
                                          │  │ (embeddings)     │   │
                                          │  └──────────────────┘    │
                                          └─────────┬────────────────┘
                                                    │ poll
                                                    ▼
                              ┌────────────────────────────────────────┐
                              │       Worker Process (同机 or GPU)     │
                              │  ┌───────────────────────────────┐     │
                              │  │   Plugin Runtime (Python)     │     │
                              │  │   - jina-clip-v2 (ONNX / PT)  │     │
                              │  │   - Qwen3-VL (Ollama/vLLM)    │     │
                              │  │   - InsightFace buffalo_l     │     │
                              │  │   - faster-whisper (v1.1)     │     │
                              │  │   - PySceneDetect / ffmpeg    │     │
                              │  └───────────────────────────────┘     │
                              └────────────────────────────────────────┘
```

> **现状说明**：M0 实际跑的是「单进程 `hometrove serve`（API + worker 后台线程）」；上图「Worker Process 同机/GPU 拆分」与模型常驻是 M1 接入真实模型后的目标形态。`faces/persons/tags/albums` 等表 M0 已建 `persons`/`face_embeddings`。

### 6.2 分层职责

| 层 | 职责 | 不应做的事 |
|---|---|---|
| Browser | 渲染、交互、SSE 订阅进度 | 直接跑模型、直接访问媒体文件 |
| API Server | 鉴权、路由、检索调度（编排 search 请求、做 RRF 融合）、jobs 入队、SSE 进度推送 | 常驻模型、跑重型推理 |
| Worker | 执行插件、产出 `plugin_results`、写 jobs 状态、报告进度 | 直接对外服务 HTTP |
| Plugin | 单一职责：在输入资产上做一件分析、返回 JSON | 跨插件串联（用 `depends_on`）、持久化（写库交给 orchestrator） |
| Storage | SQLite 单库（含 FTS5 / sqlite-vec） | 不引入第二个数据库 |

### 6.3 数据流向（关键场景）

**索引新文件**：

```
scanner → assets(insert) → jobs.enqueue(plugins per asset)
                                              │
                                              ▼
worker.poll → orchestrator.schedule(DAG) → plugin[deps=[]](并行)
         → plugin[next-tier] → ... → plugin_results(upsert)
                                              │
                                              ▼
                                        SSE 推送进度
```

**语义搜索**：

```
GET /search?q=...&page=...
  → query_text → Qwen3-VL.expand(q)  (worker, 把查询扩写为更结构化的描述，可选)
  → jina-clip-v2.encode(q)  (worker)
  → bge-m3.encode(q.expand) (worker)
  → vec_recall_a = sqlite_vec.search(scope='image' | 'scene', vec=clip_vec, k=N)
  → vec_recall_b = sqlite_vec.search(scope='caption', vec=bge_vec,    k=N)
  → fts_recall  = fts5.search(q.expand)                                    k=N)
  → rrf_merge([vec_a, vec_b, fts])                                      → 排序 → 分页
  → 取 topK 对应 asset / scene → 返回 (asset_id, t_start, t_end)
```

**视频命中跳转**：

```
GET /assets/:id?t_start=...  →  Web 端 video player set currentTime = t_start
```

### 6.4 接口隔离原则

- 任务队列：当前 SQLite `jobs` 表轮询；通过 `JobQueue` 协议抽象，未来可换 Redis/RQ/Celery 而不波及业务层。
- 向量后端：当前 sqlite-vec；通过 `VectorIndex` 协议抽象，容量超阈值可换 LanceDB。
- 模型端点：当前 Ollama（OpenAI 兼容）；通过 `VLMClient` 协议抽象，可换 vLLM / LM Studio / 远程 HTTPS。
- 鉴权：v1 关闭，但中间件钩子（`AuthBackend`）已存在，v1.1 接入即可。

---

## 7. 技术选型

### 7.1 选型表

| 层 | 选型 | 简注 |
|---|---|---|
| 后端语言 | Python 3.11 | 生态匹配（InsightFace / PyTorch / pyvips），类型系统成熟 |
| Web 框架 | FastAPI + Uvicorn + Pydantic v2 | 异步友好、自动 OpenAPI、Pydantic v2 性能优于 v1 |
| ORM / 迁移 | SQLAlchemy 2.0 + Alembic | 类型化 2.0 API；Alembic 处理 schema 演进 |
| 包管理 | pip / pyproject.toml | M0 已用 `pip install -e .`；uv 可平滑替换 |
| 数据库 | **SQLite（WAL 模式）** | 单文件部署、零运维；通过 `synchronous=NORMAL` + WAL 提升并发 |
| 向量检索 | **sqlite-vec** | 嵌入式，零部署成本；家用规模（10w~50w 向量）足够 |
| 全文检索 | SQLite FTS5 | 同库零外部依赖；中文用 `unicode61` + 自定义 `tokenchars` |
| 检索策略 | 向量召回 + FTS5 → **RRF 融合** | 单路方案对专有名词召回弱 |
| 任务队列 | SQLite `jobs` 表 + worker 轮询 | 不引入 Redis；接口隔离可换 |
| 内容理解 VLM | **Qwen3-VL-4B**（OpenAI 兼容 vision API） | 中文优秀，4B 量化后可在 8 GB 显存运行 |
| 图文向量 | **jina-clip-v2**（1024 维） | 89 语种，OpenAI CLIP 中文短板补齐 |
| 文本向量 | **bge-m3** | 中英双语强，专门编码 VLM 生成的中文描述 |
| 人脸 | **InsightFace buffalo_l**（SCRFD+ArcFace 512 维）+ ONNX Runtime | 增量质心匹配 + HDBSCAN 聚类 |
| 视频分段 | PySceneDetect（ContentDetector） | 阈值在插件配置中可调 |
| 语音转写 | faster-whisper | v1.1 引入，作为可选插件 |
| 图像处理 | libvips（pyvips）+ pillow-heif | 高性能、低内存；HEIC 支持 |
| 元数据 | 纯 Python（Pillow `getexif` + PyAV 视频元数据） | 零外部软件；pip install 即可用 |
| 视频处理 | ffmpeg / PyAV | 抽帧、缩略图、可选转码 |
| 前端 | React 19 + TS + Vite + Tailwind + shadcn/ui + TanStack Query | 现代 React 主流栈 |
| 网格布局 | react-photo-album / justified-layout | justified 网格 + 虚拟滚动 |
| 进度推送 | SSE | 比 WebSocket 简单，足够单向推送 |

### 7.2 关键选型理由

**为什么是 sqlite-vec 而不是 Qdrant / Milvus / LanceDB？**

- **部署**：单镜像 / 单文件，NAS 用户友好；Qdrant / Milvus 都引入了独立服务进程，记忆、CPU、内存都更重。
- **规模匹配**：家用库在**万级到十万级向量**（图片 + 视频场景帧）；sqlite-vec 在该区间**亚百毫秒**暴力 KNN 已可应付。突破 50w 向量再考虑 ANN（HNSW 实验中）或换 LanceDB。
- **接口隔离**：`VectorIndex` 协议抽象后，迁移成本只在 worker 一层。

**为什么是 bge-m3 而不是用 VLM 隐藏层直接做检索向量？**

- **VLM 的隐藏层不是为检索设计的**：它来自生成目标，相似度拓扑对「上下义/语义」以外的「专有名词」敏感度差。
- **文本描述向量器是专门为文本检索训练**的：bge-m3 在中英检索榜长时间 SOTA。
- **双路融合更鲁棒**：文本召回对专有名词补位；向量召回对细粒度视觉补位。

**为什么是 Qwen3-VL-4B 而不是更大的 VLM？**

- **家用部署的现实约束**：Qwen3-VL-8B / 7B 量化后显存门槛在 12~16 GB，对绝大多数家用 NAS 不现实。4B 是 8 GB 显存 + CPU/Apple Silicon 都能跑的甜蜜点。
- **接口统一**：所有 VLM 调用走 OpenAI 兼容 vision API，可换更大模型。

**为什么是 PySceneDetect 而不是 ffmpeg scenechange？**

- PySceneDetect 提供更可控的算法族（ContentDetector / ThresholdDetector），参数可暴露；ffmpeg 的 `select='gt(scene,...)'` 输出难解析。
- ContentDetector 默认能处理大多数家庭视频（镜头切换、显著运动）。

**为什么 API Server 与 Worker 拆成两个进程？**

- 模型常驻内存需要数 GB（CLIP + VLM 双装载约 5~8 GB）；不能让 Web 请求被推理排队拖垮。
- 用户可以把 worker 调度到带独显的机器，API server 留在 NAS 上跑（拆分模式）。

**为什么不直接用 Postgres 的 pgvector？**

- 部署复杂度上升一个台阶，对单用户家用场景收益边际。Postgres 适合生产级多并发；家用 SQLite 更轻。
- 项目设计上 `VectorIndex` 抽象了后端，随时可换。

**为什么是 React 19 + Vite？**

- 团队熟悉度 / 社区组件生态。
- Vite dev server 启动快，配合 `allowedHosts: ['.monkeycode-ai.online']` 可在沙箱预览域名下运行。

---

## 8. 插件系统

### 8.1 基本模型

文件分析采用可插拔形式。每个插件：

- 声明自己**能处理的媒体类型**（image / video）。
- 接收一个 `Asset` 与 `Context`，返回一段 JSON。
- 所有插件的 JSON 按**命名空间（plugin id）**独立写入，读取时合并。

### 8.2 插件接口

```python
from hometrove.plugins import (
    BasePlugin, PluginContext, Asset,
    Cost, MediaType, register,
)

class MyVLMPlugin(BasePlugin):
    id: str = "vlm.qwen3vl"
    name: str = "Qwen3-VL Captioner"
    version: str = "1.2.0"
    supported_media: set[MediaType] = {MediaType.IMAGE, MediaType.VIDEO}
    depends_on: list[str] = ["basic.scene_detect"]  # 视频需要先切场景

    class ParamsModel(BaseModel):
        prompt: str = "请用中文描述这张图片，突出主体、场景、动作、显著细节。"
        max_tokens: int = 256
        temperature: float = 0.2

    def estimate(self, asset: Asset) -> Cost:
        if asset.media_type == MediaType.VIDEO:
            return Cost(seconds=asset.estimate_scene_count() * 1.8, device="gpu")
        return Cost(seconds=0.6, device="gpu")

    def run(self, asset: Asset, ctx: PluginContext) -> dict:
        frames = ctx.frames(asset, count=8, strategy="representative")
        ctx.report_progress(0.1, "frames ready")
        out = []
        for i, f in enumerate(frames):
            desc = self._vlm.complete(prompt=ctx.params.prompt, image=f)
            out.append({"t": f.t_start, "caption": desc})
            ctx.report_progress(0.1 + 0.9 * (i + 1) / len(frames), f"captioned {i+1}/{len(frames)}")
        return {"captions": out}

register(MyVLMPlugin())
```

### 8.3 三条核心设计决策

#### 8.3.1 调度是 DAG，不是线性链

依赖关系天然不是线性的——`scene-detect` 必须先于 `face` 和 `vlm`，但后两者互相独立、可并行。

```
                    ┌────────────┐
                    │ scene-detect (video only)
                    └─────┬──────┘
            ┌─────────────┼─────────────┐
            ▼             ▼             ▼
        ┌──────┐    ┌────────┐    ┌─────────┐
        │ face │    │ vlm    │    │ (other) │
        └──────┘    └────────┘    └─────────┘
```

实现要点：

- 使用 `graphlib.TopologicalSorter` 进行拓扑排序。
- 同层节点自动并行（worker 通过 asyncio + 进程内插件 pool 执行）。
- 插件作者只需声明 `depends_on`，调度器自动展开。
- 「禁用某插件」时，下游连锁跳过（即可视化提示「关闭 vlm-caption 后，相关 embedding/scene caption 也将停跑」）。

> **现状**：M0 的 `_claim_next` 已实现「依赖插件对该资产有 done 结果才 claim」，即运行时 DAG 门控。

#### 8.3.2 Context 提供共享中间产物缓存（性能生死线）

如果每个插件各自解码图片 / 抽帧，5 个插件就是 5 次解码 5 次抽帧——对一个 4K 视频就是灾难。

`PluginContext` 必须提供：

```python
class PluginContext:
    def image(self, asset: Asset) -> Image: ...       # 缓存缩放后的图像
    def frames(self, asset: Asset, *, count: int, strategy: str): ...   # 缓存关键帧
    def result_of(self, plugin_id: str) -> dict: ...   # 读上游插件 JSON 结果
    def temp_dir(self) -> Path: ...                    # 隔离的临时目录
    def report_progress(self, frac: float, msg: str): ...
```

缓存 Key 包含 `asset_id + 关键参数`，参数变化时自动 miss。

> **现状**：`PluginContext` 已实现 `image()/frames()/result_of()/temp_dir()` 共享缓存（M1-5）：`image(max_side=)` 按需缩放并记忆化、`frames(count=, at_seconds=)` 抽帧记忆化、`result_of(plugin_id)` 读 DB 中该插件最新的 ok 结果并记忆化、`temp_dir()` 提供 `{data_dir}/plugin-tmp/{asset_id}` 隔离目录；另提供模块级 `resolve_asset_path()` 统一解析 scanned/uploads 两种路径布局。缓存 Key 含参数，参数变化时自动 miss。`face.detect` 已改为通过 `ctx.image()/ctx.frames()/ctx.result_of()` 消费共享缓存，`exif/thumbnail/basic.scene_detect` 统一走 `resolve_asset_path()`（EXIF/视频元数据与全量场景扫描无法复用像素级缓存，故各自保持本地解码）。

#### 8.3.3 结果按插件分行存储，合并只发生在读取时

**不做破坏性合并**。`plugin_results` 表按 `(asset_id, plugin_id, plugin_version)` 存储行。读取时延迟合并：

```
plugin_results:
  asset_id=42  plugin_id='face'           version='2.0.0'  status=ok    result_json={...}
  asset_id=42  plugin_id='vlm-caption'    version='1.2.0'  status=ok    result_json={...}
  asset_id=42  plugin_id='embedding'      version='0.9.1'  status=ok    result_json={...}
```

这样带来的工程好处：

- 单个插件升级或重跑**不会连累**其他插件结果。
- 可以追溯每条信息的来源插件与版本，便于审计「这条标签是哪次跑出来的」。
- 失败重试可精确到单插件，不会重新触发其他下游。

### 8.4 内置插件

| ID | 名称 | 类型 | 说明 | 状态 |
|---|---|---|---|---|
| `basic.info` | 基本信息 | 内置 | 文件名、媒体类型、`mtime`、尺寸 — **默认始终启用**，是其他插件的依赖源。 | ✅ 已实现 |
| `exif` | EXIF 元数据 | 内置 | 纯 Python：Pillow 读 EXIF（相机/镜头/ISO/曝光/GPS）+ PyAV 读视频元数据。 | ✅ M1-2 |
| `thumbnail` | 缩略图 | 内置 | Pillow 多档位（320/1280）+ PyAV 视频抽帧，写 `{data_dir}/thumbs/`（不进只读媒体根）。 | ✅ M1-1 |

### 8.5 可选插件

| ID | 名称 | 启用时机 | 依赖 | 说明 | 状态 |
|---|---|---|---|---|---|
| `basic.scene_detect` | 场景切分 | 默认 | — | PySceneDetect ContentDetector + pyav 后端（回退 opencv）；输出场景秒范围 + keyframe。 | ✅ M1-3 |
| `face.detect` | 人脸检测 | 默认 | `basic.scene_detect`（视频，非硬依赖） | InsightFace buffalo_l（SCRFD + ArcFace 512 维）on CPU（onnxruntime）；图片全帧，视频抽 keyframe 帧内去重。 | ✅ M1-4 |
| `face.match` | 人脸归组 | 默认 | `face.detect` | 消费检测 embedding，按 cosine 相似度归组到 person；命名反扫/合并见 `/api/persons`。 | ✅ M0 提前落地 |
| `vlm.qwen3vl` | 视觉语言描述 | 默认 | `basic.scene_detect`（视频） | 输出结构化 JSON 描述。 | ⬜ M1-5 |
| `embedding.jina_clip` | 图文向量 | 默认 | — | jina-clip-v2 输出 1024 维向量；视频对每个场景的关键帧独立编码。 | ⬜ M1-6 |
| `embedding.bge_m3` | 文本向量 | 默认 | `vlm.qwen3vl` | 对 VLM 输出的描述做编码，写入 `embeddings.scope='caption'`。 | ⬜ M1-6 |
| `asr.faster_whisper` | 语音转写 | v1.1 | `basic.scene_detect`（视频） | 抽取音频，转写为带时间戳的字幕；写入 `embeddings.scope='audio'`。 | ⬜ M1-10 |

> **M0 的模拟插件**：`mock.tags` / `mock.category` / `mock.faces` 不在上表——它们是 M0 为开发前端页面造的确定性模拟数据，随 M1 真实插件落地后逐步移除（`face.match` 归组管线会保留并改用真实检测结果）。

### 8.6 插件管理

`plugin_config` 表保存：

```sql
CREATE TABLE plugin_config (
  plugin_id   TEXT PRIMARY KEY,
  enabled     INTEGER NOT NULL DEFAULT 1,
  params_json TEXT NOT NULL DEFAULT '{}',
  calib       REAL    NOT NULL DEFAULT 1.0  -- EWMA 校准系数，见 §9
);
```

`/settings/plugins` 前端：根据插件自带的 `ParamsModel`（Pydantic v2）**自动渲染表单**（文本框、数值、布尔、枚举），无需前端硬编码每个插件的配置项。

支持以下三种重跑触发：

1. **插件由关转开**：扫描阶段为存量资产补建 job；只对启用状态变更后未跑过的 `(plugin_id, plugin_version)` 组合创建。
2. **插件版本升级**：检测到 `plugin_results` 中存在旧版本，自动 enqueue 重跑；新版本完成后**旧版本保留**直到用户清理（可一键删除）。
3. **单资产失败重试**：从 `jobs.error` 读取错误信息，用户点击重试只重跑对应插件，并保留 `attempts` 计数。

> 现状：`plugin_config` 表、重跑/版本后端与前端 `/settings/plugins`（开关 + 参数表单 + 定向重跑）已全部落地（M1-9）。

### 8.7 打包形式

v1 采用**进程内 Python `entry_points`**：

```toml
# pyproject.toml (示例：第三方 ASR 插件包)
[project.entry-points."hometrove.plugins"]
asr = "my_asr_pkg.plugin:FasterWhisperPlugin"
```

- Worker 进程内直接 import；模型可常驻内存。
- 零 IPC 开销，最高性能。
- 跨语言 / 跨进程插件留作后续 `ExternalPlugin` 适配器，**v1 不在范围**（防止 v1 范围蔓延）。

### 8.8 编写一个插件（示意）

最小化「关键词标签器」示例，把 VLM 描述里的名词抽取成 tags：

```python
from hometrove.plugins import BasePlugin, PluginContext, Asset, Cost, MediaType, register
from hometrove.plugins.schema import BaseModel, Field
import jieba
import re

class KeywordTagger(BasePlugin):
    id = "tag.keyword"
    name = "Keyword Tagger"
    version = "0.1.0"
    supported_media = {MediaType.IMAGE, MediaType.VIDEO}
    depends_on = ["vlm.qwen3vl"]   # 依赖 VLM 输出的 captions

    class ParamsModel(BaseModel):
        top_k: int = Field(8, ge=1, le=50)
        stopwords_extra: list[str] = []

    def estimate(self, asset: Asset) -> Cost:
        return Cost(seconds=0.05, device="cpu")

    def run(self, asset: Asset, ctx: PluginContext) -> dict:
        vlm = ctx.result_of("vlm.qwen3vl")
        captions = [c["caption"] for c in vlm.get("captions", [])]
        text = " ".join(captions)
        words = [w for w in jieba.lcut(text) if re.fullmatch(r"[一-龥A-Za-z0-9]+", w)]
        words = [w for w in words if len(w) >= 2 and w not in set(ctx.params.stopwords_extra)]
        from collections import Counter
        counter = Counter(words)
        return {"tags": [w for w, _ in counter.most_common(ctx.params.top_k)]}

register(KeywordTagger())
```

该插件会自动出现在 `/settings/plugins`，前端按 `ParamsModel` 渲染表单。

---

## 9. 索引进度与估算

### 9.1 总体方案

用任务计数百分比做进度条必然失真——2 小时 4K 视频与一张手机照片耗时差 3 个数量级。

**按预估工作量加权**：

```
progress = Σ est_cost(已完成 job) / Σ est_cost(全部 job)
```

### 9.2 estimate() 基于文件特征建模

内置插件的估算模型（v1 初版）：

| 插件 | 图片 | 视频 |
|---|---|---|
| `basic.info` | 0.01 s | 0.05 s |
| `exif` | 0.05 s | 0.2 s |
| `thumbnail` | 0.1 s（按分辨率缩放） | 0.5 s + 0.05 × 场景数 |
| `basic.scene_detect` | — | `0.04 × duration_seconds` |
| `face.detect` | `0.15 × face_count_est`（图）/ 3 s 固定（视频） | `0.6 × 场景数` |
| `vlm.qwen3vl` | 0.6 s（GPU）/ 6 s（CPU） | `1.8 × 场景数`（GPU） |
| `embedding.jina_clip` | 0.05 s | `0.08 × 帧数` |
| `embedding.bge_m3` | 0.02 s | `0.02 × 场景数` |

> ❓ 待定：上述系数为 v1 初始经验值，运行后由 §9.3 自校准持续逼近真实值。

### 9.3 EWMA 自校准（关键）

硬件差异可达 20 倍，写死系数必然不准。

```
calib[plugin] ← 0.8 × calib[plugin] + 0.2 × (actual_cost / estimated_raw_cost)
```

- `estimated_raw` 是插件 `estimate()` 的输出（按上表）。
- `actual_cost` 是 `jobs.elapsed_ms`。
- 校准系数持久化到 `plugin_config.calib`。
- 跑完前约 **20 个文件**后，进度与剩余时间预测即可达到相当准确度。

### 9.4 前端展示形态

`/settings/jobs` 页面：

- **总进度条**：百分比 + 当前已完成工作量 / 总工作量 + 预计剩余分钟数。
- **当前任务卡片**：文件名、当前插件名、已用时、子进度（在该文件上的完成率）。
- **队列视图**：按 `est_cost` 降序展示待执行任务，便于用户预估「下一个文件多久」。
- **失败列表**：`plugin_id`、`asset_id`、错误摘要、单条重试按钮。
- **暂停 / 恢复**：可暂停 worker 取新任务，正在执行的会跑完。

### 9.5 流式进度推送

- 每个插件通过 `ctx.report_progress(frac, msg)` 上报。
- Worker 聚合到一个 `(asset_id, total_frac)`，节流（~1 Hz）后通过 SSE 推送给已订阅的客户端。
- API Server 维护轻量内存订阅表，崩溃时清理；不持久化订阅（重启后页面自动重新订阅即可）。

---

## 10. 数据模型概览

> 该节是 README 用的概览，不替代完整 DDL；Alembic 迁移是唯一事实来源（`alembic/versions/`）。

```
assets          id, path, content_hash, media_type, size, mtime,
                taken_at, width, height, duration, live_photo_group

plugin_results  asset_id, plugin_id, plugin_version, status,
                result_json, elapsed_ms                        ← 核心表

embeddings      vec0 虚拟表: asset_id, scope, t_start, t_end, vec
                (scope ∈ {image, scene, caption, audio(v1.1)})

faces           asset_id, frame_time, bbox, embedding, person_id, quality
persons         id, name, info_json                            ← M0 已建
face_embeddings person_id, asset_id, embedding_json, box_json  ← M0 已建
tags / asset_tags
plugin_config   plugin_id, enabled, params_json, calib
jobs            asset_id, plugin_id, state, est_cost, actual_cost, attempts, error
albums / album_assets
persons, tags, shares(v1 占位)
```

### 10.1 两个必须强调的工程要点

**1. `assets.width / height` 必须在扫描阶段就写入。**

前端 justified 网格布局**只依赖宽高比**计算。如果你延迟到插件阶段才写，扫描后立即打开时间轴会出现所有图片都是 0×0 → 全部退化为正方形 → 滚动结束后跳变。**第一件事就是抽一帧解 metadata 写库**。

> M0 已满足：`basic.info` 在入库时写入 width/height。

**2. 视频人脸必须做轨迹去重。**

同一个人在一段视频里出现 **数百帧** 是常态。如果对每帧都存一条 `faces` 行，召回阶段会把该视频淹没。

去重策略：

- 对每个场景内的关键帧序列做帧间 **IoU 跟踪**（简单的 SORT/Kalman 即可，不引入深度模块）。
- 每条轨迹保留质量最高的 **1~3 张**脸（按 `embedding.norm()` 或检测置信度）。
- 这样 30 分钟家庭视频的人脸记录从「可能 5000 条」压到「可能 30 条」，且召回效果几乎不受影响（高质脸对聚类更有利）。

### 10.2 FTS5 中文

- tokenizer：`unicode61` + `tokenchars='_-'`
- 对中文「词」的建议：在写入 FTS5 前，对 caption 做 jieba 切词后用空格串接，存进 `fts_row` 字段；查询时同步切词。
- 或保留直接 unicode 字符级匹配作为 v1 基线（牺牲部分召回率换取零配置）。

> ❓ 待定：FTS5 是否在 v1 默认开启 jieba 切词，还是仅做英文/数字切词（v1 简化）。倾向「v1 先不切，中文纯字符级，召回略弱但实现最简；v1.1 引入 jieba」。

---

## 11. 部署形态

### 11.1 纯 Python 单进程（默认推荐）

HomeTrove 只依赖 Python 环境即可运行，无需 Docker、无需 Node。前端构建产物随仓库附带，由 API Server 直接托管。

```
┌──────────────────────────────────┐
│  python3 -m hometrove serve      │
│  ┌────────────────────────────┐  │
│  │  api  (uvicorn, :8080)     │  │
│  ├────────────────────────────┤  │
│  │  worker  (后台线程)        │  │
│  ├────────────────────────────┤  │
│  │  static frontend (web/dist)│  │
│  ├────────────────────────────┤  │
│  │  volume: HOMETROVE_DATA_DIR │ │
│  │    ├── hometrove.db        │  │
│  │    ├── staging/            │  │
│  │    └── uploads/            │  │
│  └─────────────┬──────────────┘  │
│                │ read-only        │
│  /mnt/photos ──┘   (HOMETROVE_MEDIA_ROOTS) │
└──────────────────────────────────┘
```

要点：

- `hometrove serve` 一个命令同时起 api 与 worker（同进程内后台线程），API 直接托管前端静态文件。
- 可选拆分：`hometrove api` 与 `hometrove worker` 独立进程运行。
- Docker 属可选部署方式（见 §11.5），非必需。

### 11.2 GPU worker 拆分（高级）

```
api service   ──►   shared SQLite ──  ◄──   gpu-worker service
                                                 (GPU 机器)
```

- API 与 GPU Worker 可部署到不同主机。
- 通过 `VLM_API_BASE` 环境变量指向远端 Ollama / vLLM。
- 数据卷通过 NFS / SMB 共享；sqlite-vec 在 NFS 上的性能需 v1 实测，存在二级缓存兜底。
- > ❓ 待定：NFS 上的 sqlite-vec 性能；如不可接受需考虑「远程 worker 只读 metadata + 写回主机的反向通道」。

### 11.3 媒体目录只读原则

- `HOMETROVE_MEDIA_ROOTS`（或用户自定义路径）默认以只读方式挂载/使用。
- 启动时校验：若应用进程对该路径有写权限，仅打印警告，不阻止；v1.1 强化为强制 read-only（用户可在高级设置中豁免）。
- 写入流量全部进入 `HOMETROVE_DATA_DIR`：`hometrove.db`、缩略图、模型缓存、临时目录、上传 staging。
- 不在原媒体上落地任何 `.thumb.jpg`、`.moments.json` 之类的副作用文件（这是很多自托管相册破坏原相册目录的常见反模式）。

### 11.4 备份建议

- `HOMETROVE_DATA_DIR/hometrove.db` 体积可能到 2~8 GB（视 VLM 描述缓存策略）。
- 媒体本体交给用户原有备份策略；本应用不替代通用备份。
- 文档明确「**绝不**把 `/data` 备份覆盖到比它更老的媒体备份上」。

### 11.5 Docker（可选）

仓库仍提供 `Dockerfile` 与 `docker-compose.yml` 作为可选部署方式（媒体只读挂载、API 由 uvicorn 提供、前端由 API 托管）。**默认推荐使用纯 Python 的 `hometrove serve`**；Docker 仅适用于希望隔离依赖或统一环境管理的场景。多架构镜像（`linux/amd64` + `linux/arm64`）构建尚未在 CI 落地。

---

## 12. 待决策事项

> 这些决策影响架构边界，README 阶段不替用户/未来维护者拍板，由实施期具体讨论与 RFC 决定。逐项进度跟踪见 [FEATURES.md](FEATURES.md#待决策事项)。

### 12.1 VLM 默认运行时

> **问题**：v1 默认将 Qwen3-VL 跑在哪种形态？
>
> - **A. 内置 llama.cpp**（部署简单，CPU 可跑但慢；GPU 用户需手动启用）
> - **B. 要求用户自备 Ollama / vLLM 端点**（启动快，但需用户先装 Ollama）
> - **C. 双模式：检测到本机 GPU 自动用 vLLM，否则用 llama.cpp**
>
> 倾向：**C**，但要观察家用 NAS 是否真有独显（多数仍是核显 / 集显）。

### 12.2 v1 范围裁剪

下列项**可以**从 v1 推迟到 v1.1，需要在实施前两周确认：

- **ASR**（faster-whisper 模型体积较大、对 GPU 要求较高）
- **地点地图**（Leaflet 是够用的，但「从零实现的地点聚类 + Cluster」需要 1~2 周）
- **共享链接 / 公开相册**（涉及 token / 鉴权 / 速率限制，时间预算敏感）

### 12.3 权限模型粒度

> **问题**：v1.1 的多用户形态选哪个？
>
> - **A. 多用户 + 各自私有库**：每人独立的 assets / albums 表前缀；权限边界清晰但数据隔离成本高。
> - **B. 单库 + 角色**（管理员 / 贡献者 / 访客）：库内容共享，权限通过 ACL 控制；体验更像群晖共享空间。
>
> 两种实现的数据模型差异极大，**必须在 v1.1 启动前定**。

### 12.4 调研中发现、尚未列入已确定信息的分歧点

1. **Live Photo 的存储形态**：iPhone Live Photo 是「一张 HEIC + 一段 .mov」打包，部分方案要求它们紧邻命名。PhotoPrism 与 Immich 的处理策略不同，HomeTrove 是否沿用？
2. **HEVC / HEIC 支持**：浏览器原生解码进度不一，依赖 Image Assistant 预生成预览。是否 v1 就要求转码，还是仅在 Web 端依赖浏览器能力 + 后端兜底 JPEG 预览？
3. **视频转码策略**：v1 是否要求服务端做统一 MP4 转码以保证播放兼容性？Or 只生成 HLS 流？两种方案对 CPU / 存储影响都很大。
4. **家目录与共享空间的物理路径映射**：Synology Photos 的「个人空间 + 共享空间」是行业内已成熟的双空间概念。本项目 v1 是只对应共享空间 + 不实现个人空间，还是先实现单空间 v1、再补个人空间 v1.1？
5. **重复检测 / 相似项**：Immich 默认包含「视觉相似聚类」与「精确重复」两类能力。是否纳入 v1？倾向：「视觉相似」做在 `/search` 的「以图搜图」路径里，不单独建相似项页面；「精确重复」可作为清理辅助工具。
6. **EXIF 写入**：是否提供「反写标签 / 描述到原文件 XMP」的能力？理论上需要写权限，与只读立场冲突；倾向 v1 不做，v2 评估。
7. **NER / 时间戳解析**：用户的自然语言查询可能含「去年」「国庆假期」「娃生日」（与「日期 + 人物」复合）。是否在 VLM 之外再做一个轻量自然语言解析层（regex + jieba 词性）？v1 倾向只识别「时间量词」（昨天、去年、2023），其他由 VLM 与 FTS 兜底。

---

## 13. 参与贡献

> ❓ 待定：项目处于早期编码阶段（M0 已完成，M1 进行中），欢迎在「待决策事项」中留下意见与提议；正式开发启动后将开放贡献者指南（CONTRIBUTING.md）、行为准则（CODE_OF_CONDUCT.md）与 RFC 流程。

### 13.1 可以现在就参与的环节

- 在 Issue 区对待决策事项发表意见（见 §12 / FEATURES.md）。
- 对 FEATURES.md 功能清单中的具体条目补充来源链接与对比依据。
- 翻译（英文 README、UI 文案）。
- 提交插件点子（在 Discussions 的 `plugins-idea` 分类）。

### 13.2 筹备期不接受的贡献

- 直接写实现代码（在 RFC 与代码骨架就绪之前，PR 不予评审）。
- 任何包含模型权重上传、绕过只读保护、绕过私有 LAN 鉴权的功能改动。

---

## 14. License

本项目计划采用 **AGPL-3.0**（或更新版本）。

> ❓ 待定：在编码启动前最终确认；倾向 AGPL 以保证网络使用场景下也强制开源（与 Immich 一致）。

---

## 附录：术语表

| 词 | 含义 |
|---|---|
| Asset | 数据库中一条媒体记录（可能对应原文件的一个物理对象）。 |
| Plugin | 一段可插拔的分析单元，输入 Asset + Context，输出一份 JSON。 |
| Context | 插件执行时的共享上下文，提供缓存、临时目录、进度上报。 |
| Plugin Result | 单个插件对单个资产的输出 JSON，单独存储。 |
| Scope | embedding 的语义范围：`image`（原图）/ `scene`（视频场景帧）/ `caption`（描述文本向量）/ `audio`（v1.1）。 |
| DAG | 有向无环图；本项目用于插件依赖调度。 |
| RRF | Reciprocal Rank Fusion；多路召回结果的融合排序方法。 |
| Justified 网格 | 等高两端对齐的瀑布流网格（区别于固定方格）。 |
| EWMA | 指数加权移动平均；本项目用于耗时估算的自校准。 |
| Live Photo | iPhone 的「会动的照片」格式，包含一张高分辨率图与一段短视频。 |
| M0 | 阶段一「基础框架」；只跑通扫描 + 入库 + 浏览/上传，已含模拟插件与人脸归组。 |
| M1 | 阶段二「插件扩展」；在 M0 骨架之上陆续接入真实插件与对应前端页面。 |
| RFC | Request For Comments；本项目对每一项超出当前阶段的结构性改动在合并前先 RFC 化的流程名称。 |
| 内置插件 | 位于主仓库 `hometrove/plugins/builtin/` 的插件，会随主仓库一起发布。 |
| 第三方插件 | 通过 PyPI / 私有 index、注册到 `hometrove.plugins` entry_points 组的插件。 |
| `content_hash` | 用于对原文件去重的轻量散列值；M0 使用 SHA-256（前若干 MB）。 |
| DAG 调度器 | 负责把「某个资产的插件依赖图」展开成并行执行计划的组件，在 M0 单插件场景下也强制走完整 prepare/running/done 流程以保持接口稳定。 |

---

<sub>本 README 是项目启动期的「需求 + 设计」总纲；任何与代码现状冲突的地方，以代码为准并反向更新本文档。**功能进度追踪以 [`FEATURES.md`](FEATURES.md) 为唯一权威。**</sub>
