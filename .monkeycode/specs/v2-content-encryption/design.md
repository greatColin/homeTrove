# 内容加密（Content Encryption at Rest）

Feature Name: v2-content-encryption
Updated: 2026-08-10

## Description

为 homeTrove 增加本地内容加密能力。用户在启用 vault 模式后：
- 磁盘上的加密内容（uploads、缩略图、关键帧）以 AES-256-GCM 密文存储
- 浏览器通过 HTTPS / HTTP 接收解密后的明文（服务端持密钥，不做 E2EE）
- vault 锁定时，加密资产 HTTP 响应为占位资源，前端 0 修改

`requirements.md` 已锁定算法选型（AES-256-GCM + Argon2id + HKDF）、威胁模型（防磁盘失窃，不防 root）、设计哲学（单点 read 入口、可选 vault 模式、不动文件名 / 路径 / DB 字段）。

## Architecture

```mermaid
graph TB
    subgraph Browser["浏览器 (HTTPS)"]
        UI[Web UI]
        UploadUI[上传组件 + 加密开关]
    end

    subgraph Server["homeTrove 服务 (FastAPI)"]
        AuthMW[AuthMiddleware]
        VaultMW[VaultSessionMiddleware]
        VaultAPI[/api/vault/*/]
        UploadAPI[/api/uploads/*/]
        MediaAPI[/api/assets/{id}/file|thumbnail|keyframes/]
        ReadEntry[read_asset_bytes<br/>统一入口]
        Encrypt[vault/crypto.py<br/>AES-GCM + Argon2id]
        Worker[plugins worker]
        Scanner[scanner]
    end

    subgraph Storage["本地存储"]
        Vault[Vault 目录<br/>v/, t/, k/]
        DB[(SQLite 明文)]
        PlainFiles[明文媒体<br/>uploads/, scanner 引用]
        Placeholders[占位资源<br/>placeholders/]
    end

    UI --> AuthMW
    AuthMW --> VaultMW
    VaultMW --> VaultAPI
    VaultMW --> UploadAPI
    VaultMW --> MediaAPI

    VaultAPI --> Encrypt
    UploadAPI --> Encrypt
    MediaAPI --> ReadEntry
    ReadEntry --> Encrypt
    ReadEntry --> Placeholders
    UploadAPI --> Vault
    MediaAPI --> Vault
    MediaAPI --> PlainFiles
    ReadEntry --> PlainFiles

    Worker --> ReadEntry
    Scanner --> ReadEntry
    Scanner --> Vault
    Encrypt --> Vault
```

**关键决策**：
1. **单点入口 `read_asset_bytes`**：所有 HTTP 文件端点 + 插件 worker + scanner 走同一函数，加密逻辑集中。
2. **vault 状态全局变量**：进程内 `VAULT` 单例持有 master_key，mlocked 内存；状态由 `VaultSessionMiddleware` 在请求级别检查。
3. **可选启用**：`HOMETROVE_VAULT_ENABLED` 环境变量；不启用 = 现状 0 改动。
4. **vault locked = placeholder**：永远返回 HTTP 200 + 固定占位文件，前端无感。

## Components and Interfaces

### 1. `hometrove/vault/crypto.py` — 加密原语

```python
# 关键函数签名

def generate_argon2id_params() -> dict:
    """返回 {'m': 64*1024, 't': 3, 'p': 1, 'salt': bytes(16)}"""

def derive_master_key(password: str, salt: bytes, params: dict) -> bytes:
    """Argon2id → 96 bytes raw master key."""

def derive_subkeys(raw_master_key: bytes) -> VaultSubkeys:
    """HKDF-SHA256 派生 5 个子密钥:
    - content_enc_key (32B, AES-256-GCM)
    - filename_enc_key (64B, AES-256-SIV, v2.x 用)
    - metadata_enc_key (32B, AES-256-GCM, v2.x 用)
    - hash_key (32B, HMAC-SHA256, v2.x 用)
    - db_key (32B, AES-Key-Wrap, wrap master_key 用)
    """

def wrap_master_key(raw_master_key: bytes, kek: bytes) -> bytes:
    """AES-Key-Wrap (RFC 3394), 32B 输入 → 40B 输出."""

def unwrap_master_key(wrapped: bytes, kek: bytes) -> bytes:
    """解包，失败抛 InvalidUnwrap."""

def generate_nonce() -> bytes:
    """12 字节 CSPRNG nonce."""

def encrypt_chunk(key: bytes, nonce: bytes, plaintext: bytes, aad: bytes) -> bytes:
    """AES-256-GCM 单 chunk 加密, 返回 ciphertext+tag(16)."""

def decrypt_chunk(key: bytes, nonce: bytes, ciphertext: bytes, aad: bytes) -> bytes:
    """AES-256-GCM 单 chunk 解密, 失败抛 InvalidTag."""
```

**Argon2id 参数**：m=64 MiB, t=3, p=1（OWASP 2026 推荐）。

**AAD 格式**：`b"htv1:" + asset_id_str.encode()`（v1 协议标识符 + asset_id 防跨行串改）。

### 2. `hometrove/vault/state.py` — 全局 vault 状态

```python
class VaultStatus(Enum):
    DISABLED = "disabled"     # HOMETROVE_VAULT_ENABLED=false
    INITIALIZED = "initialized"  # vault_state 行存在但未设密码
    LOCKED = "locked"         # 已设密码但当前进程未解锁
    UNLOCKED = "unlocked"     # 已解锁, master_key 在内存

class VaultState:
    status: VaultStatus
    subkeys: VaultSubkeys | None   # 仅 UNLOCKED 时非空, mlocked 内存
    raw_master_key: bytes | None   # 仅 UNLOCKED 时非空, mlocked

    def setup(self, password: str) -> None:
        """INITIALIZED → UNLOCKED: 生成 salt, 派生 master_key, 写 vault_state."""

    def unlock(self, password: str) -> None:
        """LOCKED → UNLOCKED: 读 vault_state, 派生 + 解包."""

    def lock(self) -> None:
        """→ LOCKED: sodium_memzero 所有子密钥."""

    def try_unlock_session(self, cookie_value: str) -> bool:
        """从 cookie 恢复 unlock 状态 (TTL 内). 仅恢复 subkeys, 不重派生."""

VAULT = VaultState()  # 全局单例

def is_unlocked() -> bool:
    return VAULT.status == VaultStatus.UNLOCKED

def require_unlocked() -> None:
    """FastAPI dependency: 401 if not unlocked."""
```

**内存安全**：`subkeys` 与 `raw_master_key` 用 `pynacl.sodium_malloc(mlocked=True)` 分配；dealloc / lock 时 `sodium_memzero`。

**cookie 格式**：`vault_session=<opaque_token>`，token 为 `sodium_memzero_hash(subkeys.hash_key, server_secret + "vault-session-v1")`，存 Redis-like dict（实为进程内 dict）。**不存密钥派生参数**。

### 3. `hometrove/vault/paths.py` — vault 路径生成

```python
def vault_content_path() -> tuple[Path, bytes]:
    """返回 (vault_path, nonce). 路径: vault/v/{HH}/{HH}/{32hex}.c9r"""

def vault_thumbnail_path(asset_id: int, size: str) -> Path:
    """vault/t/{asset_id}/{size}.c9r"""

def vault_keyframe_path(asset_id: int, scene: int, index: int) -> Path:
    """vault/k/{asset_id}/scene-{scene}-{index}.c9r"""

def vault_dir() -> Path:
    """{data_dir}/vault/, 启动时确保存在."""

def placeholders_dir() -> Path:
    """{data_dir}/placeholders/, 启动时确保占位文件存在."""
```

**2 段目录生成**：`BLAKE3(secrets.token_hex(16)).hexdigest()` → 取前 4 字符做 `HH/HH`，剩余做文件名。

**`{32hex}.c9r` 后缀**：`.c9r` 是 homeTrove vault 加密文件标准后缀（参考 Cryptomator 的 `.c9r` 命名）。

### 4. `hometrove/vault/stream.py` — 流式加 / 解密

```python
async def encrypt_stream_to_file(
    src: AsyncIterator[bytes] | Path,
    dst_path: Path,
    *,
    asset_id: int,
    key: bytes,
    chunk_size: int = 64 * 1024,
) -> bytes:
    """流式加密 → 写文件. 返回 nonce."""

async def decrypt_stream(
    src_path: Path,
    *,
    asset_id: int,
    key: bytes,
    chunk_size: int = 64 * 1024,
    range_header: str | None = None,
) -> AsyncIterator[bytes]:
    """流式解密 → yield 明文 chunks. 支持 HTTP Range."""

async def shred_file(path: Path) -> None:
    """覆盖 → unlink (sodium_memzero 等价: open(O_WRONLY) + write(zeros) + unlink)."""
```

**Range 处理简化策略**：解密整个 chunk → 按字节切片返回。多解密的浪费换实现简单。v2.x 可优化为 chunk index 缓存。

### 5. `hometrove/vault/read.py` — 单点读取入口

```python
def read_asset_bytes(asset: Asset) -> tuple[bytes, str]:
    """统一入口. 返回 (data, mime_type).

    1. asset.encrypted_path is None → 走明文路径
    2. vault UNLOCKED → 流式解密 → 全部 bytes (适合缩略图/小图)
    3. vault LOCKED + 加密 → 返回 placeholder bytes
    """

def read_asset_stream(
    asset: Asset,
    *,
    range_header: str | None = None,
) -> tuple[AsyncIterator[bytes], str, int]:
    """流式入口. 返回 (chunks, mime_type, total_size).

    用于 HTTP StreamingResponse, 视频必须用此入口.
    """

def resolve_plain_path(asset: Asset) -> Path | None:
    """解析明文路径 (现状 _asset_path 逻辑)."""
```

### 6. `hometrove/vault/placeholders.py` — 占位资源

```python
def ensure_placeholders() -> None:
    """启动时检查 placeholders/, 缺失则生成."""

def placeholder_for_media_type(media_type: str) -> tuple[Path, str]:
    """返回 (path, mime_type). media_type ∈ {image, video, audio, text}."""
```

**占位资源列表**：
- `image.jpg`：1024×1024 浅灰色 + 锁图标 + "Locked: Enter master password to view"
- `video.mp4`：5 秒 720p 黑底 + 锁动画 + 文案
- `audio.mp3`：10 秒静音 + 提示音
- `text.txt`：纯文本 "Encrypted asset"

**生成时机**：首次启动检查缺失即生成（用 Pillow 画图，imageio 编码 mp3）。文件固定，hash 校验，重复启动不重新生成。

### 7. `hometrove/api/routes/vault.py` — vault 状态 API

```python
@router.get("/api/vault/status")
async def vault_status():
    return {
        "enabled": VAULT.status != VaultStatus.DISABLED,
        "configured": VAULT.status != VaultStatus.INITIALIZED,
        "unlocked": VAULT.status == VaultStatus.UNLOCKED,
        "total_assets": ...,         # 仅 enabled 时计算
        "encrypted_assets": ...,
    }

@router.post("/api/vault/setup")
async def setup_vault(body: {password: str, confirm: str}):
    """首次设置 master password."""

@router.post("/api/vault/unlock")
async def unlock_vault(body: {password: str}, response: Response):
    """解锁. 成功后 Set-Cookie vault_session."""

@router.post("/api/vault/lock")
async def lock_vault():
    """锁定. 清内存密钥 + 清 cookie."""
```

### 8. `hometrove/api/middleware.py` 扩展 — `VaultSessionMiddleware`

```python
class VaultSessionMiddleware:
    """在请求级别:
    1. 读 vault_session cookie
    2. 调用 VAULT.try_unlock_session(token) → 内存解锁
    3. 后续请求 VAULT.is_unlocked() 即时生效
    """
```

**TTL**：cookie TTL = `HOMETROVE_VAULT_SESSION_TTL_SECONDS`（默认 7 天，可配 0 = 不持久）。

### 9. `hometrove/uploads/__init__.py` 修改

```python
class UploadSession:
    ...
    encrypted: bool = False

    async def finalize(self, dst_path: Path, asset: Asset) -> None:
        if self.encrypted:
            # 流式加密 staging → vault
            nonce = await encrypt_stream_to_file(staging_path, vault_path, ...)
            asset.encrypted_path = str(vault_path)
            asset.encrypted_nonce = nonce
            asset.origin_path = None
            asset.media_root = "vault"
            await shred_file(staging_path)
        else:
            # 现状
            ...
```

### 10. `hometrove/plugins/builtin/thumbnail.py` 修改

```python
async def write_thumbnail(asset_id: int, size: str, image_bytes: bytes) -> Path:
    if VAULT.status == VaultStatus.UNLOCKED:
        # 加密写入 vault
        path = vault_thumbnail_path(asset_id, size)
        nonce = await encrypt_to_file(path, image_bytes, asset_id=asset_id)
        return path
    elif VAULT.status == VaultStatus.DISABLED:
        # 明文 (现状)
        path = data_dir / "thumbs" / str(asset_id) / f"{size}.jpg"
        path.write_bytes(image_bytes)
        return path
    else:
        # LOCKED → skip, warn
        raise SkipAsset("vault locked")
```

### 11. `hometrove/plugins/builtin/keyframes.py` 同 10。

### 12. `hometrove/scanner/__init__.py` 修改 — vault 复制 hook

```python
async def ingest_new_asset(asset: Asset, plain_path: Path):
    settings = get_settings()
    if settings.vault_auto_import and VAULT.status == VaultStatus.UNLOCKED:
        # 流式加密复制到 vault
        nonce = await encrypt_stream_to_file(plain_path, vault_path, asset_id=...)
        asset.encrypted_path = str(vault_path)
        asset.encrypted_nonce = nonce
        asset.origin_path = f"{plain_path.parent}\0{plain_path.name}"
        asset.media_root = "vault"
        asset.path = str(vault_path)
    else:
        # 现状
        asset.path = f"{plain_path.parent}\0{plain_path.name}"
```

### 13. 前端组件

```typescript
// web/src/lib/vault.ts
export async function getVaultStatus(): Promise<VaultStatus>
export async function unlockVault(password: string): Promise<boolean>
export async function lockVault(): Promise<void>
export async function setupVault(password: string): Promise<boolean>

// web/src/components/VaultUnlockModal.tsx
// web/src/components/VaultSetupPage.tsx
// web/src/components/UploadEncryptedToggle.tsx
```

**`VaultUnlockModal`**：首页加载时自动检测，未解锁则弹出。提供密码框 + 解锁按钮 + 跳过按钮 + 关闭 X。

**`VaultSetupPage`**：`/setup` 路由，首次启动引导用户设置密码。

**`UploadEncryptedToggle`**：上传组件内的复选框，vault locked 时禁用。

## Data Models

### 新增表 `vault_state`

```sql
CREATE TABLE vault_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),  -- 单行表
    kdf_salt BLOB NOT NULL,                -- 16B
    kdf_params_json TEXT NOT NULL,          -- {"m": 65536, "t": 3, "p": 1}
    wrapped_master_key BLOB NOT NULL,       -- 40B (AES-Key-Wrap of 32B master_key)
    version INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
```

### `assets` 表新增字段

```sql
ALTER TABLE assets ADD COLUMN encrypted_path TEXT;        -- vault 内的密文路径
ALTER TABLE assets ADD COLUMN encrypted_nonce BLOB;       -- 12B nonce
ALTER TABLE assets ADD COLUMN origin_path TEXT;           -- 明文原路径（迁移期）
```

**迁移兼容**：
- 现有 `assets.path` 字段保留（明文资产仍用）
- `encrypted_path` 默认为 NULL（明文资产）
- 老数据 0 改动
- 新扫描 / 新上传的加密资产：`encrypted_path` 写入，`path` 改写为 vault 路径（与 `encrypted_path` 相同），`media_root = "vault"`

### `plugin_results` 表

不修改。新增 `status='skipped'` 值用于 "vault locked, asset skipped" 场景。

### Alembic 迁移 `0009_vault_state.py`

```python
def upgrade():
    op.create_table(
        'vault_state',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('kdf_salt', sa.LargeBinary, nullable=False),
        sa.Column('kdf_params_json', sa.Text, nullable=False),
        sa.Column('wrapped_master_key', sa.LargeBinary, nullable=False),
        sa.Column('version', sa.Integer, nullable=False, server_default='1'),
        sa.Column('created_at', sa.Integer, nullable=False),
        sa.Column('updated_at', sa.Integer, nullable=False),
        sa.CheckConstraint('id = 1', name='vault_state_singleton'),
    )
    op.add_column('assets', sa.Column('encrypted_path', sa.Text, nullable=True))
    op.add_column('assets', sa.Column('encrypted_nonce', sa.LargeBinary, nullable=True))
    op.add_column('assets', sa.Column('origin_path', sa.Text, nullable=True))

def downgrade():
    op.drop_column('assets', 'origin_path')
    op.drop_column('assets', 'encrypted_nonce')
    op.drop_column('assets', 'encrypted_path')
    op.drop_table('vault_state')
```

## Correctness Properties

| 不变量 | 检验方式 |
|---|---|
| Round-trip：encrypt → decrypt = 原文件 | unit test `crypto_test.py` |
| Argon2id 派生确定性 + 失败密码 unwrap 失败 | unit test |
| 加密文件 magic 头 `HTV1` | parser test |
| 占位资源在 vault locked 时固定返回（与 asset 内容无关） | integration test |
| vault locked 时 HTTP `/file` 返回 200 + placeholder bytes | smoke test |
| 解锁 cookie TTL 内可恢复 unlock 状态 | session test |
| 加密上传 finalize 后 staging 明文被 shred | file IO test |
| 锁定后内存中 master_key 区域为 0 | ptrace / `memsearch` test |
| 现有 136 个 smoke test 在 vault DISABLED 下全部通过 | regression test |

## Error Handling

| 错误场景 | 行为 |
|---|---|
| 用户输入错误 master password | `401 Unauthorized`，不区分密码错误 vs vault_state 损坏 |
| vault_state 文件损坏 / 缺列 | 启动失败，提示用户备份 vault_state 后 `setup` 重置（数据全失，需用户确认） |
| 加密文件被外部破坏 / magic 头丢失 | `decrypt` 抛 `InvalidMagic`，HTTP 500，提示用户重新导入 |
| 磁盘满 / IO 错误（加密上传 finalize） | 回滚：删 vault `.partial` + 删 DB row + 删 staging |
| vault 锁定后 worker 调度加密相关插件 | 跳过 + `status=skipped` + warn 日志 |
| Argon2id 派生超时（密码极慢派生） | 启动期硬超时 5s，否则启动失败（防 DoS） |
| 进程崩溃后 master_key 残留内存 | 依赖 mlock + 关 swap；不保证 100% 覆盖 |

## Test Strategy

### 单元测试 (`tests/test_vault_crypto.py`)

- Argon2id 参数化（m/t/p/salt）
- HKDF 派生 5 子密钥的确定性 + domain separation
- AES-GCM 加解密 round-trip
- AES-Key-Wrap / Unwrap round-trip
- 流式加密文件 → 流式解密 = 原文件（1KB / 1MB / 100MB）
- magic 头解析
- 错误密码 unwrap 失败

### 集成测试 (`tests/test_vault_api.py`)

- `GET /api/vault/status` 在三种状态下的响应
- `POST /api/vault/setup` 成功 / 密码过短
- `POST /api/vault/unlock` 正确 / 错误密码
- `POST /api/vault/lock` 清内存
- cookie 持久化 + TTL 过期

### 端到端测试 (`tests/test_vault_e2e.py`)

- 启用 vault → setup → unlock → 上传加密 → 解密读 → lock → 占位读
- scanner vault 模式新文件加密 + 老文件保留明文
- 缩略图插件 vault unlocked 时加密写 / locked 时 skip
- 公开分享链接 vault locked 时返回占位图
- HTTP Range 请求加密视频

### 兼容性测试（regression）

- 默认 `HOMETROVE_VAULT_ENABLED=false` 下，136 个现有 smoke test 全绿
- 启停 vault 不影响其他功能

## Implementation Plan（tasklist）

```
T1. [infra] 新增依赖到 pyproject.toml + 锁
T2. [model] 写 Alembic 迁移 0009_vault_state.py
T3. [model] 更新 hometrove/models.py (VaultState + assets 新字段)
T4. [crypto] 实现 hometrove/vault/crypto.py
T5. [state] 实现 hometrove/vault/state.py (含 sodium_malloc)
T6. [paths] 实现 hometrove/vault/paths.py + placeholders.py
T7. [stream] 实现 hometrove/vault/stream.py (流式 + shred)
T8. [read] 实现 hometrove/vault/read.py (统一入口)
T9. [api] 实现 hometrove/api/routes/vault.py
T10. [api] 扩展 middleware.py (VaultSessionMiddleware)
T11. [api] 改写 hometrove/api/routes/assets.py (file/thumbnail/keyframes 走 read entry)
T12. [api] 改写 hometrove/api/__init__.py (lifespan + 占位生成)
T13. [uploads] 改写 hometrove/uploads/__init__.py (加密 finalize)
T14. [plugin] 改写 thumbnail.py + keyframes.py (vault 写入)
T15. [scanner] 改写 scanner/__init__.py (vault 复制 hook)
T16. [config] 改写 hometrove/config.py (新增 vault_* 配置)
T17. [frontend] 写 vault.ts API + VaultUnlockModal + VaultSetupPage + UploadEncryptedToggle
T18. [test] 写 test_vault_crypto.py + test_vault_api.py + test_vault_e2e.py
T19. [doc] 更新 README + 部署运维文档 (关 swap / core dump)
T20. [verify] 跑全套测试 + 部署到预览验证
```

## References

- `.monkeycode/docs/encryption-research.md` — 调研报告（算法选型、市面产品对比、威胁模型）
- `.monkeycode/specs/v2-content-encryption/requirements.md` — 需求文档
- (Cryptomator) https://cryptomator.org/developers/ — vault 数据结构参考
- (gocryptfs) https://github.com/rfjakob/gocryptfs — AES-GCM chunked 加密参考
- (Cryptography) https://cryptography.io/en/latest/ — Python `cryptography` 库文档
- (Argon2 RFC 9106) https://datatracker.ietf.org/doc/html/rfc9106
- (OWASP Password Storage Cheat Sheet) https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
- (libsodium) https://doc.libsodium.org/ — `sodium_mlock` / `sodium_memzero`
- (AES-Key-Wrap RFC 3394) https://datatracker.ietf.org/doc/html/rfc3394
- (HKDF RFC 5869) https://datatracker.ietf.org/doc/html/rfc5869

## Open Questions

1. **加密上传的 staging 大小**：当前 chunks 4 MB，finalize 时一次性合并成 staging → 加密。如果用户上传 1 GB 文件，staging 临时占用 1 GB 磁盘。是否需要"边合并边加密"？MVP 接受临时占用（家庭场景单文件 < 100 MB 居多）。

2. **HTTP Range 加密视频**：MVP 走"完整 chunk 解密 + 切片"（浪费但简单）。如果浏览器播放 4K HEVC 视频，多解密可能影响首帧。v2.x 优化为 chunk 索引 + 精确寻道。

3. **vault_session cookie 的服务端存储**：MVP 进程内 dict（重启丢）。多 worker / 多实例部署需要 Redis。MVP 阶段 homeTrove 单进程足够。

4. **占位视频文件大小**：5 秒 720p mp4 ≈ 200 KB。每次 HTTP 请求都发这个字节。可以接受（HTTP 缓存 + range 友好）。