# 需求文档：本地内容加密（Content Encryption at Rest）

## 背景

homeTrove 当前所有媒体文件（扫描导入的、用户上传的、插件生成的缩略图 / 关键帧）都以明文形式存储在本地磁盘上。任何能拿到磁盘镜像或备份的人都能直接读取所有照片与视频。

调研文档 `.monkeycode/docs/encryption-research.md` 已对加密算法、密钥派生、市面产品（Cryptomator、gocryptfs、CryFS、rclone crypt 等）做了系统对比，并明确 homeTrove 的产品定位（单用户 / 单机 / 自托管 / 个人相册）与威胁模型（**主要威胁是磁盘失窃 / 备份外泄；不强求防 root**）。

调研结论的核心取舍：
- **不做端到端加密（E2EE）**：homeTrove 是自托管服务，服务器是可信边界；E2EE 带来的复杂度（每会话解锁、密码丢失即数据全失、HTTP Range 与加密字节流联调）对个人相册不划算。
- **做静态加密（encryption at rest）**：服务端持有 master_key，HTTP / HTTPS 把解密后的明文传给浏览器；磁盘失窃看不到明文。
- **先做"内容加密"，文件名 / 路径 / DB 字段加密推迟到后续阶段**：本次 spec 只覆盖内容密文、master password 解锁、上传加密开关；文件名 / 路径 / 索引加密留作 v2.x 增量扩展。

## 目标

1. 用户为 homeTrove 启用 vault 模式后，磁盘上的所有 homeTrove 拥有 / 生成的内容（uploads、缩略图、关键帧）以密文形式存储。
2. 磁盘失窃 / 备份外泄场景下，攻击者无法读取任何明文图片、视频、缩略图。
3. 用户体验：单 master password 解锁；未解锁状态可继续浏览明文资产，加密资产显示占位资源。
4. 与现有所有业务（扫描、上传、浏览、搜索、插件执行、公开分享、智能相册、地图、回收站）100% 兼容。
5. 为后续阶段（文件名加密、路径加密、SQLCipher 加密 DB）保留扩展点，本次不破坏现有架构。

## 非目标

- **不**做端到端加密（master_key 永远不发给浏览器）。
- **不**改 scanner 当前"只读扫描 media_roots"的行为；vault 模式新增可选的"复制到 vault"流程，老数据 / 老明文资产不受影响。
- **不**加密文件名、路径、DB 字段（推到后续 spec）。
- **不**重写 homeTrove 当前的 SQLAlchemy 模型（仅增量加字段 / 表）。
- **不**实现密钥找回机制；密码丢失即数据不可恢复（参考 Apple Advanced Data Protection 的强假设）。

## 术语表

- **Vault**：homeTrove 的加密容器目录，对应文件系统上一个独立目录 `{data_dir}/vault/`。所有加密内容存于此。
- **Master Password**：用户设置的 vault 主密码，用于派生 master_key。
- **Master Key**：从 Master Password 通过 Argon2id 派生的 32 字节密钥，常驻 homeTrove 进程内存（mlocked）。
- **Vault Unlock**：vault 解锁状态，分为 `locked`（未持有 master_key）与 `unlocked`（持有）。
- **Placeholder**：vault 锁定时，加密资产的 HTTP 响应使用的占位图 / 占位视频 / 占位文本。
- **Encrypted Asset**：已加密存储的资产，`assets.encrypted_path` 字段非空。
- **Plain Asset**：明文存储的资产，`assets.encrypted_path` 字段为空，`assets.origin_path` 或 `assets.path` 指向明文文件。

## 加密算法选型（已锁定）

- 内容加密：**AES-256-GCM**（AEAD，12 字节随机 nonce / 文件，文件级 AAD）
- 密钥派生：**Argon2id**（OWASP 2026 推荐参数：m=64 MiB, t=3, p=1）
- 主密钥包装：HKDF-SHA256 派生子密钥（domain separation）
- 库选型：`cryptography`（主）、`argon2-cffi`（KDF）、`pynacl`（内存安全 / mlock）

---

## EARS 需求

### REQ-1：Vault 状态与配置

#### 1.1 vault 模式开关

**User Story**: 作为系统管理员，我希望 homeTrove 默认不强制启用 vault，以便现有用户零感知升级。

WHEN homeTrove 启动且 `HOMETROVE_VAULT_ENABLED=true` 且数据库中无 `vault_state` 行，
THEN 系统应自动创建 `vault_state` 行（status=initialized，未设置 master password），
AND 跳转到首次设置密码流程。

WHEN homeTrove 启动且 `HOMETROVE_VAULT_ENABLED=false`（默认）或未配置，
THEN 系统应保持现有行为，不要求任何密码。

WHEN homeTrove 启动且数据库中已有 `vault_state` 行（master password 已设置），
THEN 系统应进入 `locked` 状态，等待用户解锁，
AND 任何加密资产的 HTTP 读取端点返回 placeholder。

#### 1.2 master password 设置（首次）

**User Story**: 作为 homeTrove 用户，我希望首次启用 vault 时必须设置 master password，确保密钥来源唯一可控。

WHEN 浏览器访问 homeTrove 且 vault 状态为 `initialized`（未设置 master password），
THEN 前端应展示设置密码页，强制用户输入两次 master password（≥ 12 字符）。

WHEN 用户提交 master password 后，
THEN 服务端应：
- 生成 16 字节随机 salt
- 用 Argon2id(password, salt, m=64MiB, t=3, p=1) 派生 96 字节 raw master key
- 用 HKDF-SHA256 派生 content_enc_key / filename_enc_key（保留位）/ metadata_enc_key（保留位）/ hash_key（保留位）/ db_key（保留位）
- 用 AES-Key-Wrap（key = db_key）wrap 96 字节 raw master key 得到 wrapped_master_key
- 写入 `vault_state`（kdf_salt, kdf_params_json, wrapped_master_key, version=1）

WHEN `vault_state` 写入成功后，
THEN 系统应进入 `unlocked` 状态，raw master key 进入 mlocked 内存。

#### 1.3 master password 解锁

**User Story**: 作为 homeTrove 用户，我希望重启 homeTrove 后能通过 master password 解锁 vault，继续访问加密资产。

WHEN 用户在解锁弹窗输入 master password 并提交，
THEN 服务端应：
- 读 `vault_state.kdf_salt` 与 `wrapped_master_key`
- 用 Argon2id 派生 raw master key
- 用 AES-Key-Wrap unwrap → 得到 96 字节 raw master key
- HKDF 派生 5 个子密钥
- raw master key 进入 mlocked 内存
- 设置 `vault_session` cookie（HttpOnly, Secure, SameSite=Strict, TTL 7 天可配）

WHEN unwrap 失败或 Argon2id 派生结果与 HMAC 不匹配，
THEN 服务端应返回 401，不泄露失败原因（防爆破）。

#### 1.4 主动锁定

**User Story**: 作为 homeTrove 用户，我希望离开设备时能主动锁定 vault，让其他人无法访问加密资产。

WHEN 用户点击「锁定 vault」按钮或调用 `POST /api/vault/lock`，
THEN 系统应：
- sodium_memzero raw master key 及 5 个子密钥
- 清除 `vault_session` cookie
- 状态切回 `locked`

WHEN 系统收到 `SIGTERM` 或进程被 kill 时，
THEN 系统应在退出前 sodium_memzero 所有 vault 相关密钥。

---

### REQ-2：Vault 目录与文件格式

#### 2.1 vault 目录结构

**User Story**: 作为系统管理员，我希望 vault 目录结构足够深，避免目录层级泄露任何明文信息。

WHEN 系统需要写入加密内容文件，
THEN 系统应计算 vault 内路径 `vault_dir/v/{HH}/{HH}/{32字符随机}.c9r`，
WHERE HH = BLAKE3(raw_bytes).hexdigest()[:2] 与 [:4]，
AND raw_bytes 为 secrets.token_bytes(16)。

WHEN 系统需要写入加密缩略图或关键帧，
THEN 系统应使用路径 `vault_dir/t/{asset_id}/{size}.c9r` 与 `vault_dir/k/{asset_id}/{scene}-{index}.c9r`，
AND {size} ∈ {small, medium, placeholder}，
AND {scene}-{index} 来自 keyframe 插件输出的元数据。

#### 2.2 文件格式

**User Story**: 作为系统工程师，我希望加密文件格式自描述、可流式解密，便于 HTTP Range 请求。

WHEN 系统写入加密文件，
THEN 文件字节布局为：
```
[4 字节 magic "HTV1"]
[12 字节 nonce]
[8 字节 chunk0_ciphertext_len][chunk0_ciphertext+tag(16)]
[8 字节 chunk1_ciphertext_len][chunk1_ciphertext+tag(16)]
...
```
WHERE chunk_size = 64 KiB 明文，
AND AAD = b"htv1:" + asset_id_bytes（仅用于完整性，不参与位置编码）。

WHEN 系统读取加密文件并解密到 chunk 边界后，
THEN 系统应清除 chunk 解密 buffer（sodium_memzero）后再读下一 chunk。

---

### REQ-3：文件读取统一入口

#### 3.1 read_asset_bytes 入口

**User Story**: 作为 homeTrove 开发人员，我希望所有"读文件"的代码都通过 `read_asset_bytes(asset)` 一个函数，以便加密 / 明文逻辑统一。

WHEN 任意代码调用 `read_asset_bytes(asset)`，
THEN 系统应：
- IF `asset.encrypted_path` IS NULL → 走明文路径，返回明文字节
- ELSE IF vault 状态 = `locked` → 返回 placeholder 字节（按 media_type 选占位图 / 视频 / 文本）
- ELSE → 解密 `asset.encrypted_path` 对应 vault 文件，返回明文字节

WHEN 调用方传入 HTTP Range 头时，
THEN 系统应返回 `(bytes, range_supported)` 元组，由调用方包装为 StreamingResponse 或 206 Partial Content。

#### 3.2 现有 HTTP 端点切换

**User Story**: 作为 homeTrove 现有用户，我希望加密模式下所有 HTTP 媒体端点正常工作，无需前端改动。

WHEN 用户调用 `GET /api/assets/{id}/file` 且该资产是加密资产且 vault locked，
THEN 服务端应返回 `200 OK`，Content-Type = mime_from_media_type，
AND body 为占位资源字节流（Content-Length 与占位文件实际长度一致）。

WHEN 用户调用 `GET /api/assets/{id}/file` 且 vault unlocked，
THEN 服务端应返回 `200 OK`，body 为解密后的明文字节流（StreamingResponse）。

WHEN 用户调用 `GET /api/assets/{id}/thumbnail?size=small`，
THEN 服务端应通过统一入口读缩略图（与原图逻辑一致）。

WHEN 用户调用 `GET /api/assets/{id}/keyframes/{scene}/{index}`，
THEN 服务端应通过统一入口读关键帧。

WHEN 用户调用 `GET /api/public/files/{token}/{asset_id}`（公开分享），
THEN 服务端应通过统一入口读原图（加密资产在 vault locked 时返回占位图）。

---

### REQ-4：上传加密

#### 4.1 上传 UI 加密按钮

**User Story**: 作为 homeTrove 用户，我希望上传文件时可以选择是否加密存储，控制粒度。

WHEN 用户打开上传对话框且 vault 状态 = `unlocked`，
THEN 前端应展示「加密存储」复选框（默认未勾选）。

WHEN vault 状态 = `locked` 或 `initialized`，
THEN 前端应禁用「加密存储」复选框，
AND hover 显示 tooltip "请先解锁 vault 才能启用加密上传"。

WHEN 用户勾选「加密存储」并上传文件，
THEN 前端应传递 `encrypted=true` 给上传 API（`POST /api/uploads/init`）。

#### 4.2 后端加密上传

**User Story**: 作为 homeTrove 用户，我希望加密上传的内容在 finalize 后立刻从 staging 清理明文，无明文残留。

WHEN 后端收到 `POST /api/uploads/init` 且 `encrypted=true`，
THEN 系统应在 UploadSession 中标记 `encrypted=true`，AND 在 finalize 阶段加密。

WHEN 后端 finalize 加密上传时，
THEN 系统应：
- 合并 chunks → staging 明文临时文件
- 调用 `VAULT.encrypt_file_to_vault(staging_path)` 流式加密
- 写入 `vault_dir/v/.../{random}.c9r`
- 用 sodium_memzero 覆盖 staging 文件（`shred -u` 或 `open(O_WRONLY|O_SYNC) + write(zeros) + unlink`）
- DB 记录：`asset.encrypted_path = vault_path`, `asset.encrypted_nonce = nonce`, `asset.origin_path = NULL`, `asset.media_root = "vault"`, `asset.path = vault_path`

WHEN 加密上传 finalize 失败（磁盘满 / IO 错误），
THEN 系统应回滚：删除 vault 临时 `.partial` 文件 + 删除 DB 已写入的 asset 行 + 清理 staging。

WHEN 用户上传明文（`encrypted=false`），
THEN 系统应保持现有行为（chunks → staging → `uploads\0{staging_path}`），无任何改动。

---

### REQ-5：插件产物的加密存储

#### 5.1 缩略图插件加密

**User Story**: 作为 homeTrove 用户，我希望缩略图也加密存储，因为缩略图泄露的隐私程度与原图相当。

WHEN thumbnail 插件生成缩略图且 vault 已 unlock，
THEN 插件应通过 `VAULT.write_encrypted(path, data)` 写入 `vault_dir/t/{asset_id}/{size}.c9r`，
AND 不再写入 `{data_dir}/thumbs/{asset_id}/{size}.jpg`。

WHEN thumbnail 插件在 vault 处于 `locked` 状态下被调度执行，
THEN 插件应 skip 当前资产，记录 warn 日志，等待下次扫描且 vault unlock 后再处理。

WHEN vault 未启用（`HOMETROVE_VAULT_ENABLED=false`），
THEN 插件应保持现有行为（明文缩略图）。

#### 5.2 关键帧插件加密

**User Story**: 与缩略图同理。

WHEN keyframes 插件生成关键帧且 vault 已 unlock，
THEN 插件应通过 `VAULT.write_encrypted(...)` 写入 `vault_dir/k/{asset_id}/scene-{n}-{idx}.c9r`。

WHEN vault locked，
THEN 插件应 skip 并 warn。

#### 5.3 插件读文件走统一入口

**User Story**: 作为系统架构师，我希望插件读取也走统一入口，加密模式下插件能正确读取原图。

WHEN 任意插件调用 `read_asset_bytes(asset)` 或打开 asset 原文件，
THEN 系统应确保插件上下文能透明读到明文（vault unlocked）或 placeholder 字节（vault locked + 加密资产）。

WHEN 插件因 vault locked 跳过处理，
THEN 系统应在 `plugin_results` 表中写入 `status=skipped, error="vault locked"`，方便用户识别。

---

### REQ-6：Scanner Vault 模式

#### 6.1 可选 vault 复制

**User Story**: 作为 homeTrove 用户，我希望开启 vault 模式后，扫描到的新文件自动复制并加密到 vault，老明文资产保留。

WHEN scanner 发现新资产且 vault 已 unlock 且 `HOMETROVE_VAULT_AUTO_IMPORT=true`（可配），
THEN scanner 应：
- 读 `media_root/rel_path` 明文流式
- 加密写入 `vault_dir/v/.../{random}.c9r`
- DB 记录：`asset.encrypted_path = vault_path`, `asset.encrypted_nonce = nonce`, `asset.origin_path = "{media_root}\0{rel_path}"`, `asset.media_root = "vault"`, `asset.path = vault_path`

WHEN scanner 发现已存在资产（dedup 命中），
THEN scanner 应保留原 `encrypted_path` 与 `origin_path`，仅更新 mtime / size_bytes。

WHEN `HOMETROVE_VAULT_AUTO_IMPORT=false`（默认），
THEN scanner 应保持现状，仅记录 `path = {media_root}\0{rel_path}`，不复制文件。

#### 6.2 老资产迁移

**User Story**: 作为 homeTrove 用户，我希望老明文资产在迁移完成后被删除明文副本（用户手动确认后）。

WHEN 用户调用 `POST /api/vault/migrate`（v1.5 阶段，本次不实现，仅留接口），
THEN 系统应遍历 `assets.origin_path` 非空的资产，流式加密到 vault，
AND 加密成功后删除 `origin_path` 指向的明文文件。

WHEN 迁移过程中发生错误，
THEN 系统应保留 `origin_path`，允许下次重试。

---

### REQ-7：前端 UX

#### 7.1 首页 vault 状态检查

**User Story**: 作为 homeTrove 用户，我希望进入首页时自动检测 vault 状态，必要时弹出解锁框。

WHEN 前端应用启动或刷新页面，
THEN 前端应调用 `GET /api/vault/status`，
AND 根据返回的 `{ configured, unlocked, total_assets, encrypted_assets }` 决定 UI 行为：
- configured=false → 不弹框，正常浏览
- configured=true, unlocked=true → 不弹框，正常浏览
- configured=true, unlocked=false → 弹出 vault unlock modal（可关闭、可跳过）

WHEN vault unlock modal 打开时，
THEN UI 应提供：
- 密码输入框（password type）
- 「解锁」按钮
- 「跳过」按钮（关闭 modal，浏览明文资产，加密资产显示占位）
- 关闭 X（功能等同跳过）

#### 7.2 占位资源展示

**User Story**: 作为 homeTrove 用户，我希望加密资产在 vault locked 时显示清晰占位，不与真实资产混淆。

WHEN 加密资产通过 `/api/assets/{id}/thumbnail` 返回占位图，
THEN 占位图应包含锁图标 + "This image is encrypted. Unlock vault to view." 文案。

WHEN 加密视频通过 `/api/assets/{id}/file` 返回占位视频，
THEN 占位视频应为 5 秒 720p 黑底 + 锁动画 + 文案。

WHEN 加密资产在 grid 视图显示，
THEN 前端无需特殊识别（占位资源已自带视觉提示）。

---

### REQ-8：错误处理与安全

#### 8.1 内存安全

**User Story**: 作为安全审计员，我希望 vault 密钥和明文 buffer 在不再需要时立即清零。

WHEN 系统分配含敏感数据的 Python buffer（master_key / chunk plaintext），
THEN 系统应优先使用 `pynacl.sodium_malloc` 或在 dealloc 时调用 `sodium_memzero`。

WHEN raw master key 进入 mlocked 内存，
THEN 系统应使用 `pynacl.sodium_mlock`，防止被 swap 到磁盘。

WHEN 进程退出 / 异常 / `vault.lock()` 被调用，
THEN 系统应对所有 vault 相关密钥调用 `sodium_memzero`。

#### 8.2 swap 与 core dump

**User Story**: 作为运维人员，我希望 homeTrove 部署文档明确指出需要关 swap / 限制 core dump，避免密钥落到磁盘。

WHEN homeTrove 检测到 `/proc/sys/kernel/core_pattern` 允许 core dump 到磁盘，
THEN 系统应输出 WARN 日志提示用户配置 `ulimit -c 0` 或 `kernel.core_pattern=|/dev/null`。

WHEN homeTrove 检测到 swap 启用，
THEN 系统应输出 WARN 日志提示用户 `swapoff -a` 或加密 swap。

#### 8.3 占位资源保密性

WHEN 攻击者拿到 homeTrove 安装目录，
THEN 占位资源文件应不泄露 vault 是否启用 / 加密资产数量等信息（占位文件固定，与 vault 状态无关）。

---

### REQ-9：API 接口清单

| Method | Path | 用途 | 鉴权 |
|---|---|---|---|
| GET | `/api/vault/status` | vault 当前状态（configured / unlocked / 计数） | 无（返回信息不含密钥） |
| POST | `/api/vault/setup` | 首次设置 master password | 无（需 vault=initialized） |
| POST | `/api/vault/unlock` | 解锁 vault | 无（需正确 master password） |
| POST | `/api/vault/lock` | 锁定 vault（清内存密钥） | vault_session cookie |
| GET | `/api/assets/{id}/file` | 读原图 | vault_session（明文资产不需要） |
| GET | `/api/assets/{id}/thumbnail?size=` | 读缩略图 | 同上 |
| GET | `/api/assets/{id}/keyframes/{scene}/{idx}` | 读关键帧 | 同上 |
| GET | `/api/public/files/{token}/{asset_id}` | 公开分享原图 | share token |
| POST | `/api/uploads/init` | 初始化上传会话（含 `encrypted` 参数） | vault_session（encrypted=true 时） |
| POST | `/api/uploads/{id}/chunk/{n}` | 上传 chunk | session |
| POST | `/api/uploads/{id}/finalize` | 完成上传（含加密 finalize） | vault_session（encrypted=true 时） |

---

## 非功能性需求

- **性能**：单 chunk（64 KiB）AES-GCM 加解密应 < 0.5 ms（i5 + AES-NI 参考）。
- **HTTP 视频流式**：浏览器播放加密视频首帧延迟 < 100 ms（占位视频 < 50 ms）。
- **解锁延迟**：Argon2id 派生应 < 500 ms（家用硬件 i5 参考）。
- **占位资源启动生成**：首次启动如占位资源缺失，应自动生成（不阻塞启动）。
- **兼容性**：所有现有 136 个 smoke test 在 vault 关闭（默认）情况下应继续通过。
- **代码组织**：新增模块 `hometrove/vault/` 子包；不污染既有模块。
- **依赖**：`pyproject.toml` 新增 `cryptography>=42.0` / `argon2-cffi>=23.1` / `pynacl>=1.5`。

---

## 验收清单

- [ ] vault 默认关闭时，homeTrove 行为与 v1 完全一致，136 个测试全绿。
- [ ] 启用 vault 后，首次启动引导用户设置 master password。
- [ ] 重启后必须输入 master password 才能访问加密资产。
- [ ] 加密资产在 vault locked 时，HTTP 端点返回 placeholder 文件，前端零修改。
- [ ] 加密上传 finalize 后，staging 临时明文被清除，vault 目录新增 .c9r 密文。
- [ ] 缩略图 / 关键帧插件在 vault unlocked 时加密写入 vault。
- [ ] 锁定 vault 后，所有 vault 相关密钥从内存清零（ps 看不到内存中明文密钥）。
- [ ] 公开分享链接在 vault locked 时返回占位图。
- [ ] scanner 在 vault unlocked 且 `VAULT_AUTO_IMPORT=true` 时自动加密复制新文件。
- [ ] 老明文资产在 vault 启用后继续明文可读，不被强制加密。
- [ ] 文档：vault 部署运维文档 + 内存安全建议（关 swap / 限 core dump）。
- [ ] 新增测试 ≥ 20 个，覆盖 crypto / read entry / unlock flow / upload encryption。