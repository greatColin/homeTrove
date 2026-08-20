# 需求实施计划：人脸识别 v2

> 关联设计文档：`.monkeycode/specs/v2-face-recognition/design.md`
> 关联需求文档：`.monkeycode/specs/v2-face-recognition/requirements.md`

- [x] 1. 数据库 schema 改造（迁移）
   - 新建 `alembic/versions/0011_face_recognition_v2.py`
     - `RENAME TABLE face_embeddings -> faces`
     - `ALTER TABLE faces ADD COLUMN cluster_id, source_plugin_id, source_model_name, frame_index, frame_t`
     - 新建表 `face_clusters`（含 `centroid_blob LargeBinary`、`radius Float`、`face_count Integer`、`representative_face_id FK faces`、`person_id FK persons`）
     - 新建表 `face_embedding_models`（注册 buffalo_l dim=512）
   - 同步修改 `hometrove/models.py`：`FaceEmbedding` 重命名为 `Face`，新增字段映射；新增 `FaceCluster`、`FaceEmbeddingModel` 类；`Person.faces` 改名为 `Person.clusters`
   - 同步修改 4 个联动文件：`hometrove/api/routes/facets.py`、`hometrove/api/routes/assets.py`、`hometrove/smart_albums.py`（trash 已 ON DELETE CASCADE 不需改）
   - 运行 `alembic upgrade head` 验证迁移通过
   - 关联需求：Requirement 3、6；设计文档 §5、§11

- [ ] 2. 旧插件标记 stub 与 registry 清理
   - 修改 `hometrove/plugins/builtin/face_detect.py`：设置 `category = "stub"`、不再被 worker 调度
   - 修改 `hometrove/plugins/builtin/face_match.py`：同上
   - 修改 `hometrove/plugins/mock/__init__.py`：MockFacesPlugin/MockTagsPlugin/MockCategoryPlugin 标记 stub，保留注册
   - 确认 `hometrove/plugins/registry.py` 在 `start_all_enabled` 阶段跳过 stub（按 §1 中"按类别过滤"判断）
   - 关联需求：Out of Scope（不迁移）

- [ ] 3. InsightFaceRuntime 共享单例（引用计数）
   - 新建 `hometrove/insightface_runtime.py`
     - `acquire(model_name="buffalo_l") -> FaceAnalysis`：线程安全首次加载、refcount++
     - `release()`：refcount--；归零时释放实例
     - 内部异常 `ModelMissingError`：检测 `~/.insightface/models/buffalo_l/` 不存在时抛出
   - 关联需求：Requirement 1（AC4、AC5）；Requirement 6（AC4、AC5）

- [ ] 4. face.image 插件实现
   - 新建 `hometrove/plugins/builtin/face_image.py`
     - `id="face.image"`、`name="人脸识别（图片）"`、`supported_media={image}`、`depends_on=["basic.info"]`、`category="implemented"`
     - `startup()`：调用 `InsightFaceRuntime.acquire()`
     - `shutdown()`：调用 `InsightFaceRuntime.release()`（用 try/finally 保证）
     - `run()`：调用 `InsightFaceRuntime` 检测 → 输出 `{status, faces[{embedding, confidence, box}]}`
     - `ParamsModel`：det_thresh、max_faces
   - 在 `hometrove/plugins/builtin/__init__.py` 中 `REGISTRY.register(FaceImagePlugin())`
   - 关联需求：Requirement 1（AC1、AC4、AC7）

- [ ] 5. face.video 插件实现
   - 新建 `hometrove/plugins/builtin/face_video.py`
     - `id="face.video"`、`name="人脸识别（视频）"`、`supported_media={video}`、`depends_on=["basic.info"]`、`category="implemented"`
     - `startup()` / `shutdown()`：同 face.image 共享 `InsightFaceRuntime`
     - `run()`：抽帧（`ctx.frames(count=max_frames)`）+ 每帧调 InsightFace + 帧内 quality filter（confidence < det_thresh 丢弃）+ 帧间 cosine 去重（intra_video_dedup）
     - 输出 `{status, faces[{embedding, confidence, box, frame_index, frame_t}], frames_sampled}`
     - `ParamsModel`：max_frames, det_thresh, max_faces, intra_video_dedup
   - 在 `hometrove/plugins/builtin/__init__.py` 中 `REGISTRY.register(FaceVideoPlugin())`
   - 关联需求：Requirement 1（AC2、AC3、AC5、AC6）

- [ ] 6. 插件内自动归组（face_cluster.py）
   - 新建 `hometrove/face_cluster.py`
     - `cluster_faces_for_asset(session, plugin_id, model_name, asset_id, faces)`：写入 Face 行 + 按 cosine 距离匹配 FaceCluster.centroid_blob
     - `_find_match(clusters, vec)`：对每个 cluster 计算 `cosine_similarity(vec, np.frombuffer(cluster.centroid_blob))`；超 MERGE_THRESHOLD 时返回最佳匹配
     - `_update_centroid(blob, vec, old_count)`：增量均值计算
     - `_select_representative(session, cluster_id)`：选 `confidence × bbox_area` 最高
     - `MERGE_THRESHOLD = 0.75`、`MIN_FACES = 3` 常量
   - 关联需求：Requirement 2（AC1-AC6）

- [ ] 7. Worker 钩子：插件 run 完调自动归组
   - 修改 `hometrove/orchestrator/run.py`（或 worker 中的 plugin run 调用点）
     - 在 `face.image` / `face.video` 的 result 持久化后，调用 `cluster_faces_for_asset(session, plugin_id, model_name, asset_id, faces)`
   - 关联需求：Requirement 2

- [ ] 8. 检查点：face.image 端到端跑通
   - 运行 `pytest -k face_image` 通过
   - 运行 `pytest -k face_video` 通过
   - 运行 `pytest -k face_cluster` 通过
   - 关联需求：Requirement 1、2

- [ ] 9. clusters / faces API 端点
   - 新建 `hometrove/api/routes/clusters.py`
     - `GET /api/clusters`：列表，支持 `?person_id`、`?source_plugin`、`?unassigned=true` 过滤
     - `GET /api/clusters/{id}`：详情，含 faces 列表与 representative_face
     - `PATCH /api/clusters/{id}`：修改 person_id（关联/取消关联）
     - `DELETE /api/clusters/{id}`：删除 cluster，把 face.cluster_id 置 NULL
     - `POST /api/clusters/{src_id}/merge-into/{dst_id}`：合并（同 plugin+model 且同 person）
   - 新建 `hometrove/api/routes/faces.py`
     - `POST /api/clusters/{cluster_id}/faces`：将单脸转移到目标 cluster
     - `DELETE /api/faces/{face_id}`：删除单脸
     - `GET /api/faces/{face_id}`：人脸详情
   - 在 `hometrove/api/__init__.py` 注册两个 router
   - 关联需求：Requirement 3（AC3、AC5）

- [ ] 10. persons API 改造 + 模型下载端点
   - 修改 `hometrove/api/routes/persons.py`
     - `GET /api/persons/{id}` 响应增加 `cluster_ids`、`plugin_ids`（去重）、`total_faces`
     - 新增 `POST /api/persons/{id}/clusters/{cluster_id}`：关联 cluster 到 person
     - 新增 `DELETE /api/persons/{id}/clusters/{cluster_id}`：取消关联
   - 修改 `hometrove/api/routes/plugins.py`
     - 新增 `POST /api/plugins/{plugin_id}/download-model`：仅 face.image / face.video 可调，启动后台线程下载
   - 关联需求：Requirement 3（AC1-AC4）、Requirement 5（AC3）

- [ ] 11. 模型下载后台 worker
   - 新建 `hometrove/worker/download_models.py`
     - `enqueue_model_download(plugin_id) -> task_id`：起一个 daemon thread 跑 `_download_worker`
     - `_download_worker`：调 `InsightFaceRuntime.acquire()`（触发 InsightFace 自动下载到 `~/.insightface/models/buffalo_l/`）→ 释放 refcount → `startup_plugin(plugin_id)` 重启插件
     - 异常处理：捕获网络/磁盘错误，写 plugin-log，让插件 status 保持 error
   - 关联需求：Requirement 5（AC3-AC7）

- [ ] 12. 检查点：API 端到端测试
   - 运行 `pytest -k clusters` 通过
   - 运行 `pytest -k faces` 通过
   - 运行 `pytest -k persons` 通过
   - 运行 `pytest -k download_model` 通过
   - 关联需求：Requirement 3、5

- [ ] 13. 前端：person/cluster/face 三层下钻 UI
   - 修改 `web/src/lib/api.ts`
     - `FaceClusterDTO`、`FaceDTO` 类型
     - `api.listClusters({personId, sourcePlugin, unassigned})`、`api.getCluster(id)`、`api.updateCluster(id, body)`、`api.deleteCluster(id)`、`api.mergeClusters(src, dst)`
     - `api.downloadModel(pluginId)`、`api.face(id)`、`api.deleteFace(id)`、`api.reassignFace(clusterId, faceId)`
   - 修改 `web/src/routes/persons.tsx`
     - 列表项显示 plugin_ids 徽章（"图片"/"视频"）
   - 新建 `web/src/routes/persons_detail.tsx`
     - 按 source_plugin_id 分组的 FaceCluster 卡片网格
     - 每个卡片显示插件徽章、组名、face_count、representative_face 缩略图
     - "未关联识别组"区域
   - 新建 `web/src/routes/cluster_detail.tsx`
     - 人脸网格（缩略图 + 置信度）
   - 新建 `web/src/routes/unassigned_clusters.tsx`
     - 列出全部未关联识别组，支持拖拽/创建人员关联
   - 修改 `web/src/App.tsx` 注册新路由
   - 关联需求：Requirement 4（AC1-AC5）

- [ ] 14. 前端：插件行模型下载按钮
   - 修改 `web/src/routes/plugins.tsx`
     - 插件 status=error 且 detail 含"缺少模型"时显示"下载模型并重启"按钮
     - 点击调 `api.downloadModel(pluginId)`，mutation 期间显示"下载中…"，成功后 invalidateQueries 让 status 变 active
   - 关联需求：Requirement 5（AC1、AC2）

- [ ] 15. 旧测试清理与新测试补全
   - 删除 `tests/test_smoke.py` 中三个旧测试：
     - `test_face_grouping_creates_unnamed_persons`
     - `test_face_match_creates_person_via_mock`
     - `test_face_match_assigns_new_face_to_existing_person`
   - 新增 `tests/test_smoke.py` 中的新测试：
     - `test_face_image_plugin_runs_on_image_asset`
     - `test_face_video_plugin_runs_on_video_asset`
     - `test_face_cluster_auto_groups_within_plugin`
     - `test_face_cluster_separates_across_plugins`
     - `test_face_cluster_min_faces_promotes_representative`
     - `test_person_can_absorb_clusters_from_multiple_plugins`
     - `test_insightface_runtime_refcount_shared_between_plugins`
     - `test_insightface_runtime_releases_when_refcount_zero`
     - `test_plugin_download_model_endpoint_triggers_startup`
     - `test_face_image_plugin_error_status_when_model_missing`
     - `test_merge_clusters_rejects_different_model`
     - `test_delete_cluster_clears_face_cluster_id`
   - 关联需求：全部

- [ ] 16. 最终检查点
   - 运行全套 `pytest` 通过
   - 运行 `python -c "from hometrove.api import create_app; create_app()"` 确认启动无报错
   - 运行前端 `npm run build` 通过
   - 关联需求：全部

## 实施提示

- 开发阶段无迁移要求：旧 Person/Face 数据可以保留在 DB 不动（schema 迁移兼容），但前端不会显示 stub 插件的结果，新数据走新管线。
- InsightFace `FaceAnalysis` 是延迟 ~50ms 导入的重量级对象，缓存机制必须保留（当前已有 `_APP` 单例，替换为引用计数版本）。
- `centroid_blob` 必须用 `numpy.ndarray.tobytes()` 存，`np.frombuffer(blob, dtype=np.float32)` 读取，不要走 JSON 序列化路径。
- 跨插件手动关联 UI 推荐 react-dnd 或 @dnd-kit/core（如果项目已有则复用，否则引入轻量库）。
- InsightFace 模型目录约定：`~/.insightface/models/{model_name}/{detection|recognition}.onnx`。