# 加密相册/资产库技术方案对比报告

> 调研日期：2026-08-10  
> 项目：homeTrove（本地家庭相册，B/S 架构，Python FastAPI + React）  
> 目标：在不破坏 homeTrove 现有扫描/上传/搜索/分享架构的前提下，为"扫描导入的照片/视频 + 文件名"设计一套工业级、可落地的加密方案，并给出分阶段实施路径。

---

## 1. 执行摘要

1. **首选算法：XChaCha20-Poly1305 作为内容加密主力，AES-256-GCM 作为兼容/可选分支。** homeTrove 部署场景横跨 Linux 服务器（多数家用 NAS）、Windows/macOS 桌面和移动浏览器（React）。ChaCha20 在没有 AES-NI 的设备上吞吐比 AES-GCM 高 3-5 倍，是 Linux ARM NAS 与移动端的默认。两者都是 IETF AEAD（RFC 8439、RFC 5116），安全性等价；当服务器 CPU 支持 AES-NI 时，GCM 才能反超 ChaCha20。
2. **文件名必须加密，但路径结构可保留（Cryptomator 风格）。** homeTrove 数据库里已有 `media_root\0relative_path` 的寻址方式 + 文件名搜索/缩略图，需要可枚举目录 + 可索引文件名。采用 **AES-256-SIV 加密文件名 + AES-256-GCM/XChaCha20-Poly1305 加密内容 + 平铺 2 段目录混淆（d/AB/...）** 的混合方案：同名同目录密文相同（允许 SQL 索引命中），跨目录密文不同（防跨目录关联），平铺存储（防目录树信息泄露）。
3. **KDF 必须用 Argon2id，scrypt 作为 fallback。** scrypt 是 Cryptomator/rclone/gocryptfs 三家的事实标准，但 OWASP 已把 Argon2id 列为首选。homeTrove 是本地单用户/小团队应用，密码派生有预算，推荐 `m=64 MiB, t=3, p=1`（OWASP 加强版）；服务端 Web 场景可降到 `m=19 MiB, t=2, p=1`。
4. **架构选择：FUSE 不适合 homeTrove，文件级加密（应用层）才是正解。** homeTrove 是 B/S 架构、必须能从 Python 直接读写加密文件，且需要支持 Windows/macOS 客户端浏览器。FUSE 要求客户端 OS 支持且应用必须能挂载，Cryptomator 通过 Dokany/WinFsp/macFUSE 才覆盖全平台。卷级加密（LUKS）只防失窃，不防运行时读取，对 homeTrove 的威胁模型帮助有限。**结论：homeTrove 走"应用层文件级加密"路线，最像 Cryptomator，但实现完全内置于 Python。**
5. **建议 Python 端主选 `cryptography`，辅以 `argon2-cffi`（KDF）和 `pynacl`（libsodium 绑定，敏感内存）。** Tink 适合 KMS/信封加密场景，homeTrove 本地无外部 KMS，过度复杂。`pycryptodome` 维护活跃度低于 `cryptography` 且内存安全不如后者。**不推荐用 Fernet**：它是 AES-128-CBC + HMAC-SHA256 的高阶封装，对 homeTrove 来说粒度不够（不可控 nonce、不可流式分块、不可控 AAD）。
6. **E2EE 不是 homeTrove 当前必须的。** E2EE 意味着浏览器要拿到密钥（用户输入密码 → 派生 → 浏览器解密），技术复杂度高、用户体验差、与现有 HTTP 视频流式播放兼容性差。**MVP 阶段做"静态加密（encryption at rest）"**：服务器持有密钥，加密落盘、明文仅在内存中临时出现。这能挡住磁盘失窃、备份外泄、文件系统被遍历三种最常见威胁。**完整 E2EE 是进阶阶段的可选项**，适合未来"跨设备同步/云备份"场景。
7. **数据迁移走"双写 + 后台重加密"模式**：保留明文目录不可变，对新增/上传文件直接加密落盘；后台 worker 异步扫描旧明文文件、加密后写新路径、删除明文。元数据（DB 中 `assets.filename`、`assets.path` 等）同步更新。

---

## 2. 威胁模型

homeTrove 是家庭/个人相册 + 资产库，威胁按可能性和危害排序：

| 级别 | 威胁场景 | 防护目标 | MVP 覆盖 | 完整 E2EE 覆盖 |
|------|----------|----------|----------|----------------|
| **T1 高** | 磁盘/整机失窃、备份介质外泄（NAS 硬盘被拔走、Time Machine 磁盘丢失、云备份桶泄露） | 静态文件 + 文件名不可读 | ✅ | ✅ |
| **T2 高** | SQL 注入、错误的目录遍历漏洞、auth 失效导致攻击者枚举/下载任意原图 | 数据库内容、文件名、明文缩略图不可直接泄露 | ✅（加密文件名 + 缩略图加密） | ✅ |
| **T3 中** | 入侵 homeTrove 进程的本地恶意软件、root/admin 攻击者在磁盘挂载/解密后读取所有数据 | 进程内明文最小化、mlock + memzero | ✅（缓解） | ✅ |
| **T4 中** | Core dump / swap 写盘泄露运行中明文 | mlock + 关 swap + 关 core | ✅ | ✅ |
| **T5 低** | 服务器机房内部人员（云托管时）物理接触磁盘 | 同 T1 | ✅ | ✅ |
| **T6 低** | 国家级对手、冷启动攻击、内存总线嗅探 | 不在范围 | ❌ | ❌ |
| **T7 极低** | 浏览器侧中间人、XSS | HTTPS 即可，不在加密范围 | ❌ | ❌ |

**不在威胁范围内**：浏览器侧 E2EE 浏览器 XSS、网络中间人（HTTPS 处理）、量子对手（需要 PQC 算法迁移，本报告不展开）、密钥本身被钓走（用户密钥管理）。

**对扫描导入路径的特殊考虑**：当前 homeTrove 假设 `media_root` 是只读挂载（`hometrove/scanner/__init__.py:65` 中 `iter_paths(root, followlinks=False)` + `hometrove/api/__init__.py:60` 的 `run_for_settings(read_only_check)`）。这意味着**"扫描导入的照片"实际上不在 homeTrove 控制下写入**，它们位于外部只读卷（如手机 SD 卡导入后的归档盘）。攻击 T1 拿到这种卷时，**homeTrove 加密落盘后该卷的内容就是明文**——所以必须做"导入即加密"：scanner 检测到未加密文件、加密写一份到加密盘、保留原盘只读（或可选删除）。MVP 阶段可保留双份（明文只在只读盘上），但同步删除模式是推荐。

---

## 3. 算法选型建议

### 3.1 推荐主选：**XChaCha20-Poly1305**

| 项 | 取值 |
|----|------|
| 算法 | XChaCha20-Poly1305（IETF AEAD_CHACHA20_POLY1305） |
| 密钥 | 32 字节（256 位） |
| Nonce | 24 字节（192 位）随机，由 CSPRNG 生成（不可计数递增） |
| Tag | 16 字节（128 位） |
| 块大小 | 推荐 256 KiB/chunk（含 nonce + tag ≈ +40 字节，开销 0.015%） |
| 参考 | RFC 8439、libsodium `crypto_aead_xchacha20poly1305_ietf_*` |

**为什么选它**：

- **192 位 nonce**：随机生成，碰撞概率可忽略。比 AES-GCM 的 96 位 nonce 更宽容——24 字节随机数要重复一次需要生成 `2^96` 个 nonce（生日界），对家用场景"无限容量"是事实上的安全。
- **无 AES-NI 也能跑得快**：ChaCha20 是纯 ARX（add-rotate-xor）运算，所有 ARM、所有没有 AES-NI 的 x86、旧手机 CPU 都能跑到接近内存带宽上限。在 Raspberry Pi 4、NAS、Synology、ARM NAS（homeTrove 主力场景）上，AES-GCM（无 AES-NI）只有 100-300 MB/s，XChaCha20-Poly1305 能跑到 500+ MB/s。
- **并行可分块**：加密是流密码 + Poly1305 一次性 MAC。允许把大文件切成 256 KiB 块独立加密，每块用不同 nonce，完美适配 homeTrove 视频/RAW 的大文件流式场景。
- **工业参考**：WireGuard、SSH（chacha20-poly1305@openssh.com）、TLS 1.3、libsodium 默认、Cloudflare、Tailscale 都用它。

### 3.2 推荐次选：**AES-256-GCM**

| 项 | 取值 |
|----|------|
| 算法 | AES-256-GCM（AEAD_AES_256_GCM） |
| 密钥 | 32 字节 |
| Nonce | 12 字节（96 位），**对每个块必须唯一** |
| Tag | 16 字节 |
| 块大小 | 推荐 32 KiB（与 Cryptomator 兼容）或 4 KiB（与 gocryptfs 兼容） |
| 参考 | NIST SP 800-38D、FIPS 197 |

**为什么保留它**：

- **AES-NI 加速**：现代 Intel/AMD 服务器（x86-64-v3+）上 AES-NI 单核可达 5-8 GiB/s，是 ChaCha20 的 1.5-2 倍。
- **FIPS 合规**：某些企业/政府合规场景需要 AES-GCM（FIPS 140-3 验证）。
- **生态兼容性**：Cryptomator/gocryptfs 默认都支持 AES-GCM，未来如需兼容现有加密 vault 格式可以走 AES-GCM。

### 3.3 不推荐 AES-CBC/CTR/XTS 单用

- **AES-256-CBC**：需要手动 padding、IV 管理、易受 padding oracle 攻击。Cryptomator 早期用过 CBC，gocryptfs 也明确指出 CBC 的 prefix leak 问题（虽然 AES-SIV/CBC 模式 + HMAC 可以修复，但维护成本高）。**禁用**。
- **AES-256-CTR**：不是 AEAD，需要另外 HMAC，nonce 重用时与 GCM 同样致命（key/IV reuse → 完整性崩塌 + 明文异或泄露）。如果用 CTR + HMAC-SHA256，请直接用 GCM。
- **AES-256-XTS**：磁盘级加密专用（IEEE 1619），每个块用 sector 号 + tweak 作" IV"，但 **XTS 不是 AEAD**，无法检测篡改。LUKS 用 XTS 是因为 sector 损坏本来就被文件系统检测（journal），但 homeTrove 的应用层文件需要 AEAD 保护（恶意篡改一个字节就让 JPEG 解码失败是可接受的，但让攻击者把 A 的内容替换成 B 的内容不可接受）。**XTS 只用于 dm-crypt/LUKS，不用于应用层**。

### 3.4 AES-256-SIV：仅用于文件名，不用于内容

AES-SIV（RFC 5297）提供**确定性 AEAD**：相同明文 + 相同 key + 相同 AAD（associated data）产出相同密文。这正是文件名加密需要的（同名同目录必须产生同密文，才能在数据库/文件系统里索引）。但 SIV 是两次 AES pass，性能约为 GCM 的一半，nonce 必须用合成 IV（Synthetic IV = MAC of plaintext），所以**只用于文件名/路径这类小数据**，不用于几 GB 的视频。

**Cryptomator 用 AES-SIV 加密文件名**，gocryptfs 用 AES-EME（也是确定性 wide-block）。两者等价，homeTrove 选 AES-SIV（更标准化）。

---

## 4. 算法对比表

| 算法 | 类型 | Nonce 大小 | AEAD | nonce 复用后果 | x86 + AES-NI 单核吞吐 | ARMv8 单核吞吐（无 Cryptocell） | 适用场景 | 在 homeTrove 的角色 |
|------|------|-----------|------|----------------|---------------------|-------------------------------|----------|---------------------|
| **AES-256-GCM** | AEAD | 96 bit | ✅ | 灾难性（key stream 异或泄露 + tag 伪造） | 5-8 GiB/s | 100-300 MB/s（无硬件加速） | 现代服务器、有 AES-NI 的桌面 | 内容加密（次选） |
| **AES-256-SIV** | AEAD（确定性） | 128 bit Synthetic IV | ✅ | 较宽容（同 IV 重复不会泄露 key stream，但泄露明文是否相同） | 2.5-4 GiB/s | 60-150 MB/s | 文件名、密钥包装 | 文件名加密 |
| **ChaCha20-Poly1305** | AEAD | 96 bit | ✅ | 与 GCM 同等 | 3-5 GiB/s（软件） | 1-2 GiB/s | 软件实现、ARM | 内容加密（次选） |
| **XChaCha20-Poly1305** | AEAD | 192 bit | ✅ | 实际不可能（生日界 2^96） | 2.5-4 GiB/s | 800 MB-1.5 GiB/s | 推荐默认（nonce 宽容） | 内容加密（首选） |
| **AES-256-XTS** | 非 AEAD | tweak（sector 号） | ❌（仅机密性） | sector 重放攻击 | 4-7 GiB/s | 80-250 MB/s | 磁盘/卷级 | 不适用 |
| **AES-256-CBC + HMAC-SHA256** | EtM 加密 + MAC | 128 bit IV | 间接 | IV 重复泄露第一个 block 异或 | 1-2 GiB/s | 100-300 MB/s | 遗留系统 | 不推荐 |

> 数据来源：
> - SUPERCOP / eBACS benchmark（DJB 团队公开测试）
> - Intel AES-NI 白皮书（5-8 cycles/byte for AES-GCM）
> - Apple ARMv8.6 Cryptography Extensions 文档（AES + SHA 加速）
> - libsodium 官方 benchmark（ChaCha20-Poly1305 ~1 GB/s per core on ARM Cortex-A53）
> - Cryptomator 实测（"Cryptography Internals" 报告）：i5-3470 AES-GCM 写 482 MiB/s、读 944 MiB/s；Cryptomator 自身因 Java overhead 写到 57 MiB/s、读 113 MiB/s
> - gocryptfs 实测：i5-3470 AES-GCM 写 482 MiB/s、读 944 MiB/s（[nuetzlich.net/gocryptfs/comparison](https://nuetzlich.net/gocryptfs/comparison/)）
> 
> 注：吞吐因 CPU 型号、OpenSSL 版本、Tink/cryptography 后端差异较大，**单核 1 GiB/s 是真实生产负载下的合理下限估算**。

---

## 5. 文件名加密策略

### 5.1 三种方案对比

| 方案 | 确定性 | 索引能力 | 安全权衡 | 实现复杂度 | 代表产品 |
|------|--------|----------|----------|------------|----------|
| **A. 概率加密**（随机 nonce） | ❌ | ❌（同名每次不同） | 最高（即使泄露整个 vault，也无法判断哪个文件叫什么） | 低 | rclone crypt "off"、naive 自己写 |
| **B. 确定性加密**（Synthetic IV / EME） | ✅ | ✅（同名同目录密文相同） | 中（泄露后攻击者可知"哪些文件同名"，可做目录级指纹，但内容还是加密的） | 中 | Cryptomator AES-SIV、gocryptfs AES-EME |
| **C. 混合方案**（SIV + 平铺 + per-directory IV） | ✅（同目录） | ✅（可索引） | 高（目录结构也被混淆，同名跨目录密文不同） | 高 | **Cryptomator（综合 A+B+C）** |

### 5.2 推荐方案 C：Cryptomator 风格

homeTrove 现状分析：

- `assets.path = f"{media_root}\0{relative_path}"` 把 `media_root` 和 `relative_path` 用 `\0` 分隔存库，`filename` 是 basename（`hometrove/scanner/__init__.py:117`）
- 文件名在多个地方用作展示、搜索、缩略图 key
- 缩略图路径是 `thumbs/{asset_id}/{size}.jpg`，**已经是 ID 化的，不依赖明文文件名**（`hometrove/api/routes/assets.py:359`）
- HTTP API 用 `asset_id`（int）寻址，不用路径

**结论**：homeTrove **不需要把"文件名"做成可加密索引**——前端拿到的是 `asset_id`，所有 DB 索引都是 ID，文件名只作为展示。**这意味着可以走方案 A（概率加密）甚至更激进的"完全抹除明文文件名"**。

但有两个例外：
1. **搜索功能**（`search.py`）可能用文件名搜索——如果未来要支持文件名全文搜索，需要方案 B/C 的确定性。
2. **EXIF 时间/相机/位置**等元数据如果存在 DB 里，需要单独加密。

**推荐采用方案 C**：用 AES-SIV + per-directory IV + 平铺 2 段目录。

**目录结构示意**（`{vault_root}/d/{HH}/{32字符密文}/`）：

```
vault/
├── d/
│   ├── AB/
│   │   └── CDEF1234567890ABCDEF1234567890AB/        # dirId 派生
│   │       ├── SIV密文文件名1.c9r                    # 文件
│   │       ├── SIV密文文件名2.c9r/
│   │       │   └── dir.c9r                          # 子目录（存 dirId 明文但加 AEAD）
│   │       └── ...
│   └── CD/
│       └── ...
├── vault.config.json                                # vault 元数据
└── masterkey.json                                   # 加密的 master key
```

**实现细节**（参考 Cryptomator 实现）：

```python
# 伪代码
DIR_ID_ROOT = ""
dir_id = uuid4().hex                              # 创建目录时生成
dir_id_hash = base32(sha1(AES_SIV_encrypt(dir_id, masterkey)))[:32]
storage_path = f"d/{dir_id_hash[:2]}/{dir_id_hash[2:]}"

# 文件名加密
filename_ciphertext = base64url(
    AES_SIV_encrypt(
        plaintext=filename.encode("utf-8"),
        key=filename_key,           # 派生自 master key
        associated_data=[dir_id]   # 用父目录 dirId 作为 AAD → 跨目录同名密文不同
    )
)
on_disk = f"{filename_ciphertext}.c9r"
```

**长文件名 fallback**：超过 220 字符（Cryptomator 阈值）的文件名，额外存一份 `name.c9s`（SHA-1 hash 索引）+ `contents.c9r`（实际文件）。**homeTrove 现实场景文件名很少超过 220 字符**，但需要保留兜底。

### 5.3 元数据加密

`assets` 表里以下字段都需要考虑：

| 字段 | 当前是否明文 | 加密建议 |
|------|--------------|----------|
| `path`（`media_root\0rel_path`） | ✅ 明文 | MVP 不加密（路径是 vault 内的密文路径，且只有 `media_root` 段泄露 vault 外部结构）。进阶阶段也加密。 |
| `filename` | ✅ 明文 | **MVP 必加密**——是用户语义的核心 |
| `content_hash` | ✅ SHA-256 | **保留明文但用 HMAC**——dedup 需要可比较性。HMAC(content_hash, master_key) 让攻击者无法对比已知明文，但 homeTrove 内部可以对比 HMAC。**或更简单：用 BLAKE3 keyed hash** |
| `size_bytes`、`mtime`、`width/height`、`duration_sec` | ✅ 明文 | **MVP 不加密**——gocryptfs 的威胁模型显示 size 指纹攻击有局限（需要 Eve 已知文件集），homeTrove 是个人相册，威胁低 |
| `taken_at` | ✅ 明文（时间戳） | **MVP 不加密**（时间戳对个人相册隐私价值低） |
| `plugin_results.result_json`（含 EXIF、GPS） | ✅ 明文 JSON | **MVP 加密**——GPS 坐标泄露家庭住址！用 AES-GCM 加密 result_json，key 派生自 vault_key |
| `asr_transcripts.text`（语音识别文本） | ✅ 明文 | **MVP 加密**——语音内容高度敏感 |
| `embeddings.embedding_json`（向量） | ✅ 明文 | **MVP 不加密**——向量本身就是模糊化处理 |

**结论**：**必须加密**：filename、plugin_results.result_json、asr_transcripts.text。**不加密**：path、size、mtime、time、dimensions、embedding。content_hash 用 HMAC 化或 keyed hash。

---

## 6. KDF 选型与参数

### 6.1 OWASP 推荐（2026 版）

| 算法 | 推荐参数 | 内存 | 适用 |
|------|----------|------|------|
| **Argon2id** | m=19 MiB, t=2, p=1 | 19 MB | 最低线 |
| | m=46 MiB, t=1, p=1 | 46 MB | 备选（不与 Argon2i 共用） |
| | m=12 MiB, t=3, p=1 | 12 MB | 备选 |
| | m=9 MiB, t=4, p=1 | 9 MB | 备选 |
| | m=7 MiB, t=5, p=1 | 7 MB | 备选 |
| **scrypt** | N=2^17 (128 MiB), r=8, p=1 | 128 MB | Argon2id 不可用时 |
| | N=2^16 (64 MiB), r=8, p=2 | 64 MB | |
| | N=2^15 (32 MiB), r=8, p=3 | 32 MB | |
| **PBKDF2-HMAC-SHA256** | 600,000 iterations | — | FIPS-140 必需 |
| **bcrypt** | cost ≥ 10 | 4 KB | 仅遗留系统 |

参考：[OWASP Password Storage Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)

### 6.2 homeTrove 推荐参数

**Argon2id 是首选**，因为：
- 现代参数化（内存硬），对 GPU/ASIC 攻击天然抵抗
- Argon2i 数据独立防侧信道，Argon2d 抗 GPU，Argon2id 混合
- 已有 Python `argon2-cffi` 绑定（libargon2）
- Argon2 是 PHC 比赛冠军，标准化（RFC 9106）

**homeTrove 推荐配置**（家用场景，妥协安全与可用性）：

| 场景 | m | t | p | 派生时间（i5 参考） | 内存峰值 |
|------|---|---|---|---------------------|----------|
| **本地桌面 / 服务器（推荐默认）** | 64 MiB | 3 | 1 | ~250 ms | 64 MB |
| **NAS / 低算力服务器** | 19 MiB | 2 | 1 | ~80 ms | 19 MB |
| **移动 App（React Native）** | 12 MiB | 3 | 1 | ~150 ms | 12 MB |
| **Web 浏览器派生（不推荐：会卡顿）** | 19 MiB | 2 | 1 | ~300 ms（JS 慢 3 倍） | 19 MB |

**派生后产出三个 key**（domain separation）：

```
master_key = Argon2id(password, salt, m, t, p, output=96 bytes)
            ↓  HKDF-SHA256 with info="hometrove-vault-v1"
content_enc_key    = HKDF(master_key, salt=opaque_aead, info="content-enc-v1")[:32]
filename_enc_key   = HKDF(master_key, salt=opaque_aead, info="filename-enc-v1")[:32]
metadata_enc_key   = HKDF(master_key, salt=opaque_aead, info="metadata-enc-v1")[:32]
```

**Domain separation 至关重要**：三个 key 用途完全不同（一个用于 AES-GCM 内容、一个用于 AES-SIV 文件名、一个用于 metadata），混用会导致 cross-protocol attack（如用 SIV key 做 GCM 加密）。参考 [NIST SP 800-108](https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication800-108.pdf) 和 [libsodium crypto_kdf](https://doc.libsodium.org/key_management)。

### 6.3 不推荐 PBKDF2 的原因

- **不是 memory-hard**：GPU 上 PBKDF2-HMAC-SHA256 可以跑 100 GH/s（hashes per second），单卡 RTX 4090 暴力破解 8 字符密码只需几小时。Argon2id 在同等硬件上只有 200-500 H/s。
- **homeTrove 没有 FIPS-140 合规需求**：所以 PBKDF2 唯一的优势（FIPS 验证）不适用。
- **scrypt 与 Argon2id 对比**：scrypt 已经比 PBKDF2 好（128 MiB 内存硬），但 Argon2id 在新型硬件上优势更明显，且 Argon2id 的并行化（p 参数）比 scrypt 灵活。

**PBKDF2 只在以下情况使用**：必须 FIPS 合规、或密码库只支持 PBKDF2（极老环境）。

---

## 7. 三种架构路线对比

| 维度 | **A. 文件级应用层加密** | **B. FUSE 文件系统** | **C. 卷级加密（LUKS / FileVault）** |
|------|------------------------|--------------------|-----------------------------------|
| **透明性** | 应用代码改写读写路径 | 完全透明，应用不改 | 完全透明，但需要 mount/unmount |
| **明文落盘范围** | 加密 vault 内全部密文 | 挂载点内全部明文（明文在内存/cpu registers） | 整个磁盘（除加密卷外） |
| **支持平台** | 全平台（跨 OS 一样） | 需要 OS FUSE 支持（Linux 原生、macOS 需 macFUSE、Win 需 Dokany/WinFsp） | 各 OS 原生（BitLocker/FileVault/LUKS） |
| **Python 集成** | 直接 read/write 加密文件 | 需要 subprocess 调用挂载/卸载，或 python-fuse | 完全无关 |
| **HTTP 集成** | 直接走 FastAPI StreamingResponse | 同 | 同 |
| **缩略图/预览** | 应用自己控制加密 + 缓存 | 透明，但需要解密生成缩略图 | 同 B |
| **增量加密** | ✅ 友好（一个文件一个文件迁） | ⚠️ 需要一次性迁整个挂载 | ❌ 必须一次性迁整个卷 |
| **元数据保护** | ✅ 强（每个文件名都加密） | ✅ 强 | ❌ 弱（mtime/size/filename 都在 inode 明文） |
| **威胁：磁盘失窃** | ✅ 文件全加密 | ✅ 同 | ✅ 同 |
| **威胁：进程运行时读取** | ⚠️ 取决于应用实现 | ❌ 明文在文件系统层 | ❌ 同 |
| **威胁：服务器管理 root** | ⚠️ root 可读取明文数据 | ⚠️ root 可读取明文（挂载后） | ⚠️ 同 |
| **多人/多用户** | ✅ 每个 vault 一个 master key | ⚠️ 共享挂载会泄露 | ❌ 不支持 |
| **homeTrove 契合度** | ✅ **高**——可与现有 scanner/api/uploads 直接对接 | ❌ 中——FUSE 在 macOS/Windows 上是负担，且 homeTrove 是 B/S 架构服务器端，不需要透明挂载 | ❌ 低——LUKS 在 NAS 上需要 mount/unmount，每次启动系统要输密码，与 homeTrove 的"开机即服务"不符 |

### 7.1 homeTrove 推荐：A. 文件级应用层加密

**理由**：

1. homeTrove 是 B/S 架构：**服务器持有密钥**，用户通过浏览器访问。FUSE 给单用户桌面场景设计（Cryptomator），但 homeTrove 不需要"挂载到本地目录"——它只需要 Python 进程能读写加密文件。
2. **平台独立性**：homeTrove 部署在 Linux（Docker）、macOS、Windows、NAS 上，FUSE 在每个 OS 都要不同的客户端库，应用层加密只用一个 Python 库（`cryptography`）就能跨平台。
3. **HTTP 流式传输**：当用户在浏览器里请求 `/api/assets/123/file` 时，FastAPI 用 `StreamingResponse` 直接把解密后的字节流喂给 HTTP——FUSE 在这一步没有任何优势（FS 读本身就是流式），但应用层加密能精确控制 Range 请求（HTTP 206 Partial Content）的处理。
4. **缩略图**：homeTrove 已经有 thumbnail 插件（`hometrove/plugins/builtin/thumbnail.py`），生成 `{data_dir}/thumbs/{asset_id}/small.jpg`。**加密状态下 thumb 也要加密**（保护隐私），这在 FUSE 下需要文件系统层额外写一份加密副本，应用层直接写加密文件即可。
5. **元数据**：FUSE 暴露明文 filename 和 mtime 给 OS（因为 inode metadata 是明文），这泄露文件名。**应用层可以全加密**。
6. **数据迁移**：FUSE 必须一次性把整个目录解密重加密，应用层可以逐文件异步加密（旧文件继续可读，新文件直接加密），用户体验更好。

### 7.2 不推荐 B 和 C 的具体原因

**B. FUSE 路线不适合 homeTrove 的原因**：
- Cryptomator 的成功证明 FUSE 在单用户单设备上好用，但 homeTrove 多人多设备 NAS 场景下，FUSE 进程崩溃 = 整个挂载点失效
- homeTrove 已经假设 `media_roots` 是只读挂载（`hometrove/api/__init__.py:60` `run_for_settings`），加密 vault 是"输出"目录，scanner 不会去扫它
- FUSE 与 HTTP 服务有冲突：FUSE 挂载期间必须有一个进程持有 key，Docker 部署需要 sidecar container 跑 FUSE

**C. 卷级加密不适合 homeTrove 的原因**：
- LUKS / FileVault / VeraCrypt 只防 T1（磁盘失窃），不防 T2（应用层漏洞）和 T3（root 攻击）
- LUKS 的 `mtime` 和 `filename` 都是明文 inode（除非用 ecryptfs 内核层）
- LUKS mount 需要密码，启动时人工介入，与 homeTrove 自动化部署冲突

**homeTrove 推荐组合**：A + C（可选）。
- **必须**：A（应用层文件加密）。
- **可选**：C（系统磁盘 LUKS/BitLocker/FileVault）作为额外防御层，特别是笔记本/便携设备部署时。

---

## 8. 市面产品参考表

| 产品 | 内容算法 | 文件名 | KDF | 密钥存储 | 元数据隐藏 | 性能 | 优点 | 缺点 |
|------|----------|--------|-----|----------|-----------|------|------|------|
| **Cryptomator** | AES-256-GCM（32 KiB chunk，per-chunk nonce 12B + AAD=header_nonce∥chunk_no） | AES-256-SIV + per-dir IV + SHA-1 平铺 | scrypt(N=2^15=32768, r=8, p=1) | AES-Key-Wrap (RFC 3394) 加密 master key 存 JSON | mtime/size 明文；filename/dirId 加密；dir path 混淆 | 写 57 MiB/s、读 113 MiB/s（Java overhead） | 跨平台、开源、文档详尽、有 desktop+mobile+Hub、3rd-party 审计 | Java 拖慢性能；CRLF/HFS+ 等 Windows 兼容性 |
| **gocryptfs** | AES-256-GCM 4 KiB/chunk（默认）/ AES-SIV / XChaCha20-Poly1305 | AES-256-EME wide-block + per-dir IV（gocryptfs.diriv） | scrypt | HKDF 派生 content+filename key；master key AES-GCM 加密存 conf | 同上，但 dirIV poisoning 风险明确文档 | 写 482 MiB/s、读 944 MiB/s（i5 + AES-NI） | 性能极佳、Go 单二进制、reverse mode、3rd-party 审计（Defuse 2017） | Linux-only 原生（macOS beta、Windows 需 cppcryptfs）；加密文件名长度 175 字符 |
| **CryFS** | AES-256-GCM，**分块存储**（默认 32 KiB 块，**打散文件大小指纹**） | 分块目录树（每个文件一个子目录里的多个块文件） | scrypt | 同 gocryptfs | **所有文件名/大小都被分块打散** | 写 69 MiB/s、读 99 MiB/s（CPU bound） | **抗 size fingerprinting**；分块 + 目录混淆最彻底 | 性能最差（多文件 IO）；macOS/Windows experimental；密文膨胀（每文件 ~3x overhead）；学术原型出身 |
| **rclone crypt** | XSalsa20-Poly1305（NaCl SecretBox，64 KiB chunk） | AES-256-EME（Halevi-Rogaway）+ PKCS#7 padding + base32 | scrypt(N=16384, r=8, p=1) | 用户密码 obscured（AES-CTR with static key——**仅防猫眼扫描**） | mtime/size 保留；文件长度可被推断到 16 字节精度；filename 可关（off/obfuscate/standard） | 流式加密，无 FUSE 开销 | 跨云存储；命令行/脚本友好；广泛使用 | obscured password 弱；size leak 16 字节精度；不抗主动攻击者 |
| **Bitwarden Send / Tresorit** | AES-256-GCM | N/A（文件共享） | Argon2id（Bitwarden，64 MiB/3/4） | 服务器 vault 加密，client master key + PBKDF2/Argon2 | — | — | 工业级密码管理；零知识；Bitwarden 开源 | Tresorit 闭源；Send 是一次性共享不是 vault |
| **MEGA** | AES-128-CBC（块）+ ECB（块头） | — | 自定义（PBKDF2-like） | 用户 RSA 密钥对 + AES 节点密钥 | mtime 保留；share key 重新派生 | — | 50GB 云空间；零知识；客户端 SDK 开源 | **AES-128-CBC（不是 GCM！）**：MEGA 自 2013 至今仍有争议（researcher 多次质疑 CBC 模式的 malleability），但官方坚持安全 |
| **Apple iCloud Advanced Data Protection** | AES-256-GCM（推测） | — | PBKDF2（推测） | Secure Enclave 派生 key + Recovery Key / Recovery Contact | 25 类数据 E2EE，mtime/photos 计数等元数据仍 Apple-held | — | 工业级 E2EE；recovery contact 设计优雅 | 闭源；`iCloud.com` 关闭强制 E2EE 时关闭 web 访问；shared album 不支持 E2EE |
| **Signal** | AES-256-CTR + HMAC-SHA256（Double Ratchet，每消息新 key） | N/A | X3DH（Curve25519 DH）+ HKDF-SHA256 | Identity key 在 Secure Enclave/Keystore | 消息级 ephemeral；sealed sender | — | 业界 E2EE 标杆；前向/后向保密 | 不直接对应文件加密，但是参考实现 |
| **7-Zip** | AES-256-CBC + HMAC-SHA256（仅 -mem=AES256 时） | 可选 AES-256 加密 | PBKDF2-HMAC-SHA256（默认 100k iter，password + salt） | 嵌入归档 | mtime/size 保留 | — | 普及；归档 + 加密一体 | CBC 模式 + 非 AEAD（HMAC 后置）；密钥重用问题 |
| **LUKS / dm-crypt** | AES-256-XTS（默认）+ SHA-256 HMAC（integrity 模式 LUKS2） | 不加密（inode 明文） | PBKDF2（cryptsetup luksFormat 默认） | 头部 2 MiB 存 master key + 加密的 user key（最多 32 key slot） | **不保护元数据** | 接近原生磁盘速度 | OS 层集成；性能无损失 | 不能区分文件；不支持多 vault；boot 时需要 key |

### 8.1 关键观察

**Cryptomator 是 homeTrove 的最接近参考**——同样是"加密 vault 文件夹"思路，同样 B/S 友好（虽然 Cryptomator 是 FUSE，但 vault 数据结构是应用层规范）。

**gocryptfs 的 SIV/EME 思路很优雅**——单一确定性算法保护文件名 + 平铺目录。如果 homeTrove 要做"FUSE 兼容模式"作为未来扩展，gocryptfs 是首选参考。

**CryFS 的分块存储是隐私极致但性能灾难**，**homeTrove 不应该模仿**——分块让 thumbnails/streaming 视频完全没法做（你得解密所有块才能拼回原图）。

**Apple ADP 是 E2EE 工业参考**——它的威胁模型划分（哪些数据 E2EE、哪些 metadata 留在 Apple）和 recovery 设计（recovery contact / recovery key）是 homeTrove 未来"完整 E2EE 阶段"的最佳参考。

**Signal 不是文件加密**，但 Double Ratchet 的"每消息新 key + 派生链"哲学可以借鉴——homeTrove 可以做到"每文件新 content key + master key wrap"（envelope encryption）。

---

## 9. 内存安全实践

这是用户特别关心的部分。**核心矛盾**：加密文件系统本质上是把数据"挪到"内存里解密展示，攻击者只要能在进程运行时读取进程内存（root + 内存取证 / cold boot / core dump），就能拿到明文。**这一层防护的目标不是"绝对安全"，而是"提高攻击门槛、缩短明文暴露窗口"**。

### 9.1 libsodium 的设计参考

libsodium 提供了三个层次的内存保护 API（[doc.libsodium.org/memory_management](https://doc.libsodium.org/memory_management)）：

**Level 1：`sodium_memzero`** —— 擦除明文

```c
sodium_memzero(buf, len);  // 防止编译器优化掉 memset
```

**关键点**：普通 `memset(buf, 0, len)` 可能被编译器优化掉（"dead store elimination"），因为编译器看不到后续使用。`sodium_memzero` 用 volatile 指针 + 内存屏障保证不被优化掉。

**Level 2：`sodium_mlock` / `sodium_munlock`** —— 锁定内存（防 swap 写盘）

```c
sodium_mlock(buf, len);     // 包装 mlock()/VirtualLock() + 标记不进 core dump
// ... 使用 buf ...
sodium_munlock(buf, len);   // 擦除 + 标记为可 swap
```

`mlock()` 把页面锁在物理内存里，禁止换出到 swap。这样：
- **防 swap 泄露**：即使系统开始 swap，敏感数据不会落盘
- **部分防 core dump**：libsodium 还调 `madvise(MADV_DONTDUMP)` 标记不进 core dump
- **不能防寄存器/栈**：敏感数据被读到 CPU 寄存器（解密时）会留在寄存器里，进程切换时寄存器值可能存到栈上。**libsodium 自己也承认这是限制**。

**Level 3：`sodium_malloc` / `sodium_mprotect_*`** —— 守护堆分配

```c
buf = sodium_malloc(len);   // 守护页 + canary + mlock + 不进 core
sodium_mprotect_readonly(buf);  // 临时设为只读，防意外覆写
sodium_mprotect_readwrite(buf);
sodium_free(buf);           // 擦除 + 解锁 + 释放
```

**守护页（guard page）**：在堆分配的前后放两个不可访问的页，缓冲区溢出立刻触发 SIGSEGV。比 ASLR/DEP 更严格的应用层保护。

### 9.2 Python 端怎么做

**Python 没有真正的安全内存 API**。原因：

1. `bytes` 是不可变对象，函数返回的 `bytes` 在内存里**永远擦不掉**——GC 会回收它，但不知道什么时候回收，CPython 不会主动 zero out。
2. `bytearray` 和 `memoryview` 是可变对象，可以覆写，但覆写时机和 `id()` 都受 GC 控制。
3. `cryptography` 库官方明确说：**"cryptography does not clear memory by default... like almost all software in Python is potentially vulnerable to this attack"**（[cryptography.io/en/latest/limitations](https://cryptography.io/en/latest/limitations/)）。
4. 但 `cryptography` 推荐：**"users wishing to do so can pass memoryview or another mutable type to cryptography APIs, and overwrite the contents once the data is no longer needed"**。

**最佳实践**：

```python
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import ctypes

class SecureBuffer:
    """可变 buffer + 显式擦除。"""
    def __init__(self, data: bytes):
        self._buf = bytearray(data)
    
    def get(self) -> memoryview:
        return memoryview(self._buf)
    
    def wipe(self):
        """用 ctypes 直接覆写 Python 内存，绕过 CPython 抽象。"""
        if hasattr(self, "_buf"):
            ctypes.memset(id(self._buf), 0, len(self._buf))
            # 注：上面这行需要 ctypes 技巧（实际要用 ctypes.cast + POINTER）
            self._buf = bytearray()
    
    def __del__(self):
        self.wipe()


def encrypt_file_aesgcm(key: bytes, plaintext_buf: SecureBuffer, aad: bytes):
    """加密完后立刻擦除明文。"""
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext_buf.get(), aad)
    plaintext_buf.wipe()  # 立刻擦
    return nonce, ciphertext
```

**注**：`ctypes.memset` 在 Python 上擦内存并不可靠——`bytearray` 内部数据可能在被擦前已经被 GC 复制到别处。更可靠的是用 `pynacl` 暴露的 `nacl.utils.sodium_memzero`，它在 C 层擦。

### 9.3 推荐：`pynacl`（libsodium 绑定）做密钥和敏感内存管理

```python
import nacl.utils
import nacl.secret
import nacl.bindings

# Sodium 安全分配 + 擦除
buf = nacl.utils.sodium_memzero(b"\x00" * 32)
nacl.bindings.sodium_memzero(buf)  # 显式擦
nacl.bindings.sodium_mlock(buf, len(buf))  # 锁定
# ... 使用 buf ...
nacl.bindings.sodium_munlock(buf, len(buf))  # 解锁 + 擦
```

**homeTrove 实际建议**：

- **关键密钥（master_key、KEK、content_key）**：用 `pynacl` 的 `sodium_malloc` 分配，用 `nacl.bindings.sodium_memzero` 擦。**不要用 `bytes` 存**。
- **解密后的大文件数据**：走流式分块，每块解密后立刻喂给 HTTP/缩略图管道，**不要 `read()` 整个文件**。FastAPI `StreamingResponse` 已经支持异步生成器。
- **缩略图**：缩略图是 JPEG 字节流，喂给 PIL 的 `Image.open(BytesIO(buf))` 后立刻 wipe `buf`。PIL 内部会复制像素数据。
- **mtime/size 等元数据**：不算敏感，正常 `int` / `str` 即可。

### 9.4 swap / core dump 防护

在 homeTrove 部署文档里必须强调：

```bash
# 关闭 swap（最稳）
sudo swapoff -a
# 或加密 swap
# /etc/crypttab: 确保 swap 行用 random key

# 关闭 core dump
ulimit -c 0
echo "* hard core 0" >> /etc/security/limits.conf

# Docker 部署：在 docker run / compose 里
security_opt:
  - "no-new-privileges:true"
  - "seccomp:default"
  - "cap-drop: ALL"
  - "cap-add: CHOWN"
tmpfs:
  - /tmp:rw,nosuid,nodev,size=100m  # /tmp 不落盘
```

**最有效的一招：磁盘全加密（LUKS/BitLocker/FileVault）+ 关 swap**——这是 libsodium 文档自己承认的（"if cold boot attacks or at-rest data protection are serious concerns... the most effective defense is to encrypt the entire disk volume and encrypt the swap partition"）。应用层 mlock 是 defense-in-depth，不是终极方案。

### 9.5 mmap 的注意事项

**绝对不要 mmap 磁盘上的加密文件到内存**——mmap 会把文件内容映射到进程地址空间，且 mmap 的页面**不受 mlock 控制**（mmap 的页面是 page cache 的拷贝，独立管理）。攻击者读 `/proc/<pid>/maps` 就能看到 mmap 区域。

**正确做法**：

```python
# 反例：直接 mmap 加密文件 → 密文在内存（无所谓）+ 但 mmap 操作 lazy load 时可能 page fault 进 page cache
# 如果解密需要大块读取，用 read() 而不是 mmap
with open(enc_path, "rb") as f:
    while True:
        chunk = f.read(CHUNK_SIZE)
        if not chunk:
            break
        decrypted = AESGCM.decrypt(nonce_increment(), chunk, aad)
        yield decrypted
```

### 9.6 临时文件（缩略图 / 预览）

缩略图场景（`hometrove/plugins/builtin/thumbnail.py`）：

- 当前实现：明文 JPEG 写入 `{data_dir}/thumbs/{asset_id}/small.jpg`。**加密后必须改成：加密写入 `{vault_dir}/thumbs/{asset_id}/small.jpg.c9r`**。
- 临时明文：PIL `Image.save()` 内部用 BytesIO 或 tempfile，要确保临时文件路径在 tmpfs（`/tmp`）或加密 vault 内，不要泄露到磁盘。
- **进阶**：可以用 PIL 的 `Image.tobytes()` 拿到原始 RGB，再直接加密。完全不走临时文件。

视频抽帧（`PyAV`）：PyAV 解码会写临时 buffer 在它自己内部管理，但**永远不要把原始帧写到磁盘明文**。

---

## 10. 缩略图 / 预览处理

### 10.1 三种方案对比

| 方案 | 性能 | 安全 | 实现复杂度 | 代表 |
|------|------|------|------------|------|
| **A. 缩略图明文 + 缓存加密** | 中（每次解密原图生成） | 中（缩略图明文泄露，但内容是低分辨率图像） | 低 | 默认 |
| **B. 缩略图加密存储**（每次读缩略图都解密） | **高**（缩略图小，解密快） | 高（无明文） | 中 | **Cryptomator** |
| **C. 加密原图 + 缓存加密缩略图** | 高 + 偶尔大文件解密 | 高 | 中 | **推荐** |

### 10.2 推荐方案 C

**实现**：

1. 缩略图插件（`hometrove/plugins/builtin/thumbnail.py`）先生成 PIL Image（内存中）
2. JPEG 编码到 `bytearray`（可变）
3. AES-GCM/XChaCha20-Poly1305 加密，密文写 `{vault_dir}/thumbs/{asset_id}/small.jpg.c9r`
4. **不写明文到磁盘**
5. HTTP `GET /api/assets/{id}/thumbnail?size=small`：
   - 读密文 → mlock → AES decrypt → StreamingResponse 流式输出 JPEG → 立即 wipe 内存
6. **绝不缓存明文到 `/tmp` 或 `data_dir/thumbs/` 明文**

**性能估算**：

- 缩略图 small（320 px 长边）JPEG 大约 20-50 KB
- AES-GCM 解密：50 KB 几乎瞬时（< 1 ms）
- XChaCha20-Poly1305：同样快
- HTTP 响应：浏览器缓存 + Cache-Control: private, max-age=...（如 7 天）减少重复解密

**加密密文膨胀**：
- small.jpg 50 KB → .c9r 加密文件：50 KB + 12 字节 nonce + 16 字节 tag = 50 KB + 28 字节 ≈ 50 KB（开销 0.05%）
- 可以忽略

### 10.3 视频缩略图

视频抽帧（`PyAV`）和图片缩略图一样加密，但要注意：

- PyAV 解码视频流时**会用 ffmpeg 的内部 buffer**，这些 buffer 是 ffmpeg 自己 `malloc` 的，不受 Python 控制。
- **缓解**：用 `with av.open(src) as container:` 让 PyAV 在 `__exit__` 时释放 buffer；确保 `frame.to_ndarray()` 出来的 ndarray 用完后立刻 wipe。
- 视频本身是加密存储，HTTP 视频流式播放走 HTTP Range Requests，必须支持 206 Partial Content。

### 10.4 Apple Photos 高级数据保护的启发

Apple 的 Photos 在 ADP 开启后：

- **缩略图也是 E2EE 加密的**——任何预览、编辑操作（"回忆"功能、人脸识别）的临时数据都受保护。
- **内存保护**：iOS 在 App 进入后台时强制对内存敏感区截图模糊化（防止截图泄露相册）。
- **设备锁屏后**：Secure Enclave 拒绝任何 key derivation，直到用户重新解锁。

homeTrove 借鉴：

- 后台任务运行时（worker 进程），如果 vault 是 locked 状态，**worker 不应该能解密任何文件**——worker 必须等到用户登录后获取 vault key 才能工作。
- HTTP API 在 vault unlocked 前返回 401（要求输入 master password）。

---

## 11. 现有数据迁移

### 11.1 阶段化迁移（推荐）

**Phase 1（"双写过渡期"，MVP 完成后立即启用）**：

```
现状：
- media_root 是只读挂载，含明文照片
- homeTrove 扫描后只把路径记录到 DB，文件不复制

Phase 1 之后：
- 引入 vault_dir（加密 vault 根目录，可写）
- scanner 扫描时，如果 vault_dir 已 enabled，对每个新发现的明文文件：
  1. 在 vault_dir 创建加密副本（流式：read 4KB → encrypt → write 4KB）
  2. 更新 assets.path 为 vault 内的密文路径
  3. 记录 origin_path = {media_root}\0{rel}（明文原路径，只在 DB 内）
- 旧明文路径仍可读（双备份期）
- 缩略图：vault 内加密生成
```

**Phase 2（"后台 worker 异步迁旧文件"）**：

```
后台 job：
- 遍历 origin_path 还指向明文盘的资产
- 对每个文件：
  - 如果明文源文件已被外部修改（mtime 不一致）→ skip，留给下次扫描
  - 否则：流式加密写入 vault，原子更新 assets.path，删除 origin_path
- 进度：每小时批量 commit DB
- 用户可暂停/恢复
```

**Phase 3（"硬切换 + 清理"）**：

```
- 所有 origin_path 为空的资产都已迁完
- 询问用户："是否从明文盘删除已迁文件？"
- 删除操作只在用户确认后执行（明文盘可能是只读的，扫描会失败）
- 删除模式 = rename to .trash（homeTrove 软删除风格）
```

### 11.2 迁移期间的一致性

**问题**：scanner 在迁一半时重启，会怎样？

- **DB 状态**：每文件迁完后立刻 commit（`hometrove/scanner/__init__.py:146` 的 `commit_batch` 模式），重启从断点续做
- **文件状态**：用 `.partial` 后缀写临时文件，写完后 rename 到 `.c9r`（atomic），参考 `hometrove/uploads/__init__.py:174-179` 的 `tmp -> replace` 模式
- **崩溃恢复**：扫描 `.partial` 文件 + DB 中未 commit 的标记 → 删除未完成的 `.partial`

**双写模式**：

```
# 写入路径（伪代码）
def save_asset(asset_id, plaintext):
    enc_path = vault_dir / encrypted_filename(asset_id)
    nonce = random(12)
    ciphertext = AESGCM.encrypt(content_key, nonce, plaintext, aad=header)
    
    tmp = enc_path.with_suffix(".c9r.partial")
    write_atomic(tmp, ciphertext)
    
    # DB 更新：在 vault 路径写入后
    db.execute("UPDATE assets SET path = ?, origin_path = NULL WHERE id = ?", enc_path, asset_id)
    db.commit()
    
    # 改名 + 删临时
    os.replace(tmp, enc_path)
```

### 11.3 增量加密的 CRUD 改造

homeTrove 当前 CRUD（参考 `hometrove/api/routes/assets.py` 的 `trash_asset` 等）：

- **Create**（scanner/upload ingest）：直接走加密写入路径（Phase 1 后）
- **Read**（HTTP file/thumbnail/keyframe）：解密后流式返回
- **Update**：homeTrove M0 假设媒体只读（`media_roots` 只读挂载），所以应用层从不修改原图。但 scanner 会更新 `mtime`、`size` 等元数据。
- **Delete**（trash）：明文软删除（`deleted_at` 字段）+ 真删（worker purge）。加密状态下，删除 = 删除密文文件 + DB 行。

---

## 12. Web / HTTP 场景特殊处理

### 12.1 E2EE vs 静态加密（encryption at rest）

| 项 | 静态加密（at rest） | E2EE |
|----|--------------------|------|
| **密钥位置** | 服务器持有 | 客户端（浏览器）持有 |
| **HTTPS 传输** | 服务器解密后用 HTTPS 传给浏览器 | 服务器不解密，传密文 + 客户端解密 |
| **能防 T1 磁盘失窃** | ✅ | ✅ |
| **能防 T2 SQL 注入 / 应用漏洞** | ❌（攻击者可读 DB + 调 API 取明文） | ✅（密钥不在服务器，攻击者拿不到明文） |
| **能防 T3 root 攻击** | ❌ | ✅（root 只能拿密文，密钥在用户输入） |
| **能防 T4 内存取证** | ⚠️ 缓解（mlock） | ✅（明文永远在浏览器进程） |
| **用户体验** | 简单（输一次 vault 密码即可） | 复杂（每次会话需要解锁；密码忘记 = 数据全失） |
| **HTTP 视频流式** | ✅ 简单 | ⚠️ 需要 HTTP Range + 加密字节流 + 客户端解密逻辑 |
| **多设备共享** | ✅ | ⚠️ 密钥共享协议（如 Cryptomator Hub / Bitwarden Send） |
| **备份** | ✅ 服务端可全量备份 | ⚠️ 备份需要重新分发密钥 |
| **homeTrove 推荐阶段** | **MVP** | **进阶（v2+）** |

### 12.2 MVP 阶段：服务端解密 + HTTPS

```python
# FastAPI StreamingResponse 示例
@router.get("/api/assets/{asset_id}/file")
async def get_asset_file(asset_id: int, range_header: str | None = Header(None)):
    # 1. 读密文元数据
    asset = db.get(Asset, asset_id)
    enc_path = resolve_vault_path(asset.path)  # vault 内的密文路径
    
    # 2. HTTP Range 处理
    enc_size = enc_path.stat().st_size
    
    if range_header:
        # Range 请求：解密指定字节范围
        # 注意：加密是分块的，Range 不能跨 chunk 边界（除非解密到 chunk 边界后过滤）
        # 简化做法：解密整个文件再按 byte range 切片（性能差但安全）
        # 进阶：解密时保留 chunk index，支持精确 Range（gocryptfs 做法）
        ...
    
    # 3. 流式解密 + 返回
    async def decrypt_stream():
        # mlock buffer（sodium_mlock）
        # 每次 decrypt 32 KB
        # yield 明文
        # 立刻 wipe buffer
        ...
    
    return StreamingResponse(decrypt_stream(), media_type=mime_type)
```

### 12.3 进阶阶段（E2EE）需要解决的关键问题

1. **浏览器端解密**：
   - 用 [Web Crypto API](https://developer.mozilla.org/en-US/docs/Web/API/SubtleCrypto)（`crypto.subtle.decrypt`）
   - 但 Web Crypto **不支持 XChaCha20-Poly1305**！只支持 AES-GCM、AES-CTR、CBC 等
   - **要么妥协用 AES-GCM**（浏览器原生支持）
   - **要么用 libsodium.js / tweetnacl.js**（WASM 实现 ChaCha20）

2. **密钥分发**：
   - 主密码 → Argon2id → master_key（in browser only）
   - 永远不传 master_key 给服务器
   - 服务器只存 master_key 的 wrapped 副本（用密码 + 服务器盐派生，加密后存 DB）

3. **HTTP Range + 加密**：
   - 如果客户端解密，服务器只能传密文（不解密）
   - 浏览器需要能 seek 密文 + 调 Web Crypto
   - **实际产品**：Cryptomator iOS/Android 都是本地解密（不是真正的浏览器 E2EE），因为浏览器 + HTTP Range + 流式视频的组合太复杂

4. **TLS + 加密双重**：
   - E2EE 不是替代 TLS，是补充。HTTPS 防中间人，E2EE 防服务端。

### 12.4 homeTrove 推荐路径

| 阶段 | 加密模式 | 触发场景 |
|------|----------|----------|
| **MVP（v1）** | 静态加密（at rest）+ HTTPS | 家庭/个人 NAS，磁盘失窃是主要威胁 |
| **进阶（v1.5）** | 静态加密 + 浏览器 Vault 密码验证（不传 master key） | 多用户，密码保护 |
| **完整 E2EE（v2）** | 浏览器端 Argon2id + Web Crypto AES-GCM | 公网部署、云托管、跨设备 |

---

## 13. 针对 homeTrove 的具体推荐

### 13.1 homeTrove 现状摘要

读源码后总结（`/workspace` 当前结构）：

| 组件 | 当前状态 | 加密需要修改 |
|------|---------|--------------|
| `assets` 表 | `path = {root}\0{rel}`、`filename` 明文 | `filename` 改为加密显示，`path` 改为 vault 路径 |
| `scanner` | 只读扫描，不复制文件 | 改为 "扫描 → 加密写副本 → 记录 vault 路径" |
| `uploads` | chunked upload 到 `staging/`，明文 | 改为 chunked encrypted upload（客户端预加密，或服务端流式加密） |
| `api/assets/{id}/file` | `FileResponse` 明文 | 改为 `StreamingResponse` 流式解密 |
| `api/assets/{id}/thumbnail` | `FileResponse` 明文 JPEG | 改为密文读 + 解密流式 |
| `api/public/files/{token}/{id}` | 公开分享，**明文** | 重要：分享 token 时仍可发密文，分享链接需要 vault 密码 |
| `thumbs/` 目录 | 明文 JPEG | 改为 vault 内加密 |
| `keyframes/` 目录 | 明文 JPEG | 同上 |
| `plugin_results.result_json` | 明文 JSON（含 EXIF、GPS） | **必加密** |
| `asr_transcripts.text` | 明文文本 | **必加密** |
| `embeddings.embedding_json` | 明文向量 | 不必加密（向量本身模糊化） |
| `persons.name`、`albums.name` | 明文 | 加密（用户自定义标签） |
| `auth.py` | PassthroughAuthBackend（永远 "local" principal） | **必须扩展为 Vault Unlock 机制** |

### 13.2 三阶段实施路径

#### 阶段 MVP（v1，~4-6 周工作量）

**目标**：单用户本地加密 vault，at rest 加密，浏览器 HTTPS 看明文。

**新增代码**：

```
hometrove/
├── crypto/
│   ├── __init__.py
│   ├── kdf.py           # Argon2id 派生 master_key
│   ├── aead.py          # AES-GCM / XChaCha20-Poly1305 AEAD
│   ├── filename.py      # AES-SIV 文件名加密 + dirId 派生
│   ├── master_key.py    # master_key 包装 / 解包（PBKDF2-protected JSON）
│   ├── envelope.py      # DEK/KEK 派生（HKDF domain separation）
│   ├── stream.py        # 流式加解密（chunked AEAD）
│   └── mem.py           # sodium_memzero / mlock 封装（pynacl）
├── vault/
│   ├── __init__.py      # Vault 类：open / lock / unlock / derive keys
│   ├── paths.py         # 明文路径 ↔ 密文路径映射
│   └── migrate.py       # Phase 1/2 迁移工具
```

**修改代码**：

- `hometrove/models.py`：新增 `vault_state` 表（master_key wrapped + salt + Argon2id params）
- `hometrove/api/__init__.py`：启动时检测 vault state，未配置 → 引导用户设置 master password；已配置 → 引导 unlock
- `hometrove/scanner/__init__.py`：扫描后调用 `vault.write_encrypted(asset, plaintext)` 写 vault
- `hometrove/api/routes/assets.py`：所有 `FileResponse` 改为 `StreamingResponse(decrypt_stream())`
- `hometrove/plugins/builtin/thumbnail.py`：缩略图加密写
- `hometrove/auth.py`：新增 `VaultAuthBackend`，要求请求带 vault unlock session

**新增数据库迁移**（alembic）：

```sql
-- vault_state: 单行表，存 master_key 加密参数
CREATE TABLE vault_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),  -- 只有一行
    kdf_salt BLOB NOT NULL,
    kdf_params_json TEXT NOT NULL,  -- Argon2id m/t/p
    wrapped_master_key BLOB NOT NULL,  -- master_key AES-Key-Wrap（用 KEK）
    wrapped_master_key_mac BLOB NOT NULL,  -- HMAC for integrity
    version INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- assets 表新增列
ALTER TABLE assets ADD COLUMN origin_path TEXT;  -- 迁移期存明文原路径，迁完后置 NULL
ALTER TABLE assets ADD COLUMN enc_filename TEXT;  -- 加密文件名（base64url）
ALTER TABLE assets ADD COLUMN enc_path TEXT;       -- vault 内的密文路径

-- plugin_results: result_json 加密（可选：AES-GCM + metadata_mac）
-- asr_transcripts: text 加密
```

**关键库**：

```toml
# pyproject.toml 新增
"cryptography>=42.0",       # 主选：AEAD
"argon2-cffi>=23.1",        # Argon2id KDF
"pynacl>=1.5",              # sodium_memzero / mlock / XChaCha20（如果选 XChaCha20）
# 可选
"tink>=1.7",                # envelope encryption 备选
```

**MVP 验收**：

- [ ] 用户首次启动必须设置 vault master password（无默认密码）
- [ ] 重启 homeTrove 必须输入 vault master password 解锁
- [ ] 任何磁盘镜像/备份中看不到明文文件名或图片字节
- [ ] DB 中 `filename` 是密文（base64url）
- [ ] 缩略图是密文
- [ ] HTTP 视频流式正常播放（解密 < 50 ms 延迟，肉眼无感）
- [ ] LUKS / BitLocker 文档推荐
- [ ] swap / core dump 配置文档

#### 阶段 进阶（v1.5，~6-8 周）

**目标**：多用户、密码保护 + session、可选 E2EE 浏览器模式。

- 实现 `VaultAuthBackend`（cookie session，TTL 7 天）
- 公开分享链接的密文模式（接收方需要 vault 密码解密）
- 改进流式解密：HTTP Range 精确寻道（按 chunk 边界对齐）
- 缩略图缓存策略（带缓存期 + size bucket 加密）
- 视频缩略图优化（预生成所有关键帧缩略图）
- Phase 2 后台迁移 worker

#### 阶段 完整 E2EE（v2，~12-16 周）

**目标**：浏览器端解密，服务器无密钥。

- Web Crypto AES-GCM 实现（drop XChaCha20 支持，换 AES-GCM）
- 客户端 Argon2id（用 libsodium.js 或 argon2-browser WASM）
- DEK/KEK envelope encryption（每个文件一个 DEK，DEK 用 KEK wrap，KEK 派生自主密码）
- Recovery Key（参考 Apple ADP 28 字符恢复码）
- Recovery Contact（可选，参考 Apple ADP）
- HTTP Range + 加密字节流精确寻道

### 13.3 关键配置（pyproject.toml 依赖）

```toml
[project]
dependencies = [
    # 现有
    "fastapi>=0.115",
    "sqlalchemy>=2.0",
    "pillow>=10.4",
    # 新增（加密）
    "cryptography>=42.0",      # 主选 AEAD 库
    "argon2-cffi>=23.1",       # Argon2id KDF
    "pynacl>=1.5",             # libsodium 绑定（XChaCha20 + 内存安全）
    # 暂不引入
    # "tink>=1.7",              # 信封加密（如未来对接 KMS）
    # "pycryptodome>=3.20",     # 备选（如果 cryptography 缺什么）
]
```

---

## 14. 开源库选择（Python 端）

| 库 | 推荐度 | 优点 | 缺点 | 在 homeTrove 的角色 |
|----|--------|------|------|---------------------|
| **`cryptography`（pyca）** | ⭐⭐⭐⭐⭐ | PyCA 维护，Python 生态事实标准；OpenSSL / BoringSSL 后端；FIPS 兼容；AES-GCM/AES-SIV/ChaCha20/XChaCha20 全支持；活跃审计；底层 API + high-level Fernet；文档齐全 | mlock 不暴露给 Python；不直接暴露 XChaCha20 的某些 IETF 变体；Fernet 是固定封装不可定制 nonce | **主选**：AEAD 加密（content、metadata）、KDF（HKD |
| **`argon2-cffi`** | ⭐⭐⭐⭐⭐ | CFFI 绑定 libargon2（reference C 实现）；PHC 字符串格式标准；OWASP 推荐；维护活跃 | 单独的依赖 | **主选**：Argon2id 派生 master_key |
| **`pynacl`（PyNaCl）** | ⭐⭐⭐⭐ | libsodium 绑定（DJB 团队出品）；XChaCha20-Poly1305 原生；**暴露 sodium_memzero / sodium_mlock / sodium_malloc**（Python 罕见的内存安全 API）；nacl.bindings 直通 C | 文档较薄；维护活跃但不如 cryptography | **辅选**：内存安全（敏感 buffer）、可选 XChaCha20 |
| **`tink`（Google）** | ⭐⭐⭐ | Google 出品，安全性设计（hard to misuse）；信封加密原生支持；Streaming AEAD；多语言兼容（与 Android/iOS 客户端互通）；KMS 集成 | 文档分散（Google Cloud 风格）；Python 实现较新；**对单进程本地 vault 过度设计**；需要管理 keyset handle | **暂不引入**，未来如果做 KMS 集成或跨平台客户端互通再考虑 |
| **`pycryptodome`**（PyCrypto 后继） | ⭐⭐ | 自包含（纯 Python fallback）；AES / ChaCha20 全套；允许低层操作 | **维护活跃度低于 cryptography**；安全审计历史比 cryptography 弱；与 OpenSSL 不互通（自实现）；`Crypto` 命名冲突老代码 | **备选**：仅在 cryptography 不可用时 |
| **Fernet（cryptography 子模块）** | ⭐⭐ | 一行 API：`Fernet(key).encrypt(data)` | **AES-128-CBC + HMAC-SHA256，nonce/IV 不可控**；不支持 AEAD nonce 自管；不支持 streaming；密钥格式固定；**对 homeTrove 太粗粒度** | **不推荐** |
| **`hashlib`（stdlib）** | ⭐⭐⭐⭐⭐ | 标准库；SHA-256 / BLAKE2 / HKDF；无依赖 | 不是密码学原语完整库；KDF 用 PBKDF2（OK 但 Argon2id 更好） | **用**：HKDF（domain separation）、SHA-256（content hash）、BLAKE3（如选 keyed hash） |
| **`secrets`（stdlib）** | ⭐⭐⭐⭐⭐ | 标准库；CSPRNG；token_bytes / token_hex / compare_digest | 没有 KDF | **用**：生成 nonce、salt、master_key |
| **`pyaes` / `pyDes` / 其他自实现** | ❌ | — | 自实现密码学是反模式 | **禁用** |

### 14.1 推荐组合

```python
# pyproject.toml
dependencies = [
    "cryptography>=42.0",   # 主选 AEAD
    "argon2-cffi>=23.1",    # Argon2id KDF
    "pynacl>=1.5",          # 内存安全（memzero / mlock）
]
# 可选
# "tink>=1.7",            # 未来 KMS
```

**理由**：

- `cryptography` 是 PyCA 维护、Python 生态的 cryptography 事实标准，OpenSSL/BoringSSL 后端经过无数审计。Cryptomator 的 Java 实现、gocryptfs 的 Go 实现、Tink 的 C++ 实现底层都依赖类似库（OpenSSL / BoringSSL / nacl），Python 端对应就是 `cryptography`。
- `argon2-cffi` 是 Python 上唯一主流 Argon2 实现，绑定了 reference libargon2。
- `pynacl` 提供 `cryptography` 没有的内存安全 API（sodium_memzero / sodium_mlock）。这是关键——`cryptography` 自己文档明确说"不主动擦内存"，所以敏感密钥/buffer 需要 pynacl。
- **不推荐 Tink**：Tink 的 envelope encryption + KMS 集成对 homeTrove 当前需求过度。Tink 适合"客户端 → KMS → 加密存储"的云原生场景；homeTrove 是本地单进程，master_key 直接在内存里，不需要 KMS。**未来如果做"多设备共享密钥 + 云备份"再考虑 Tink**。
- **不推荐 Fernet**：粗粒度、不可控 nonce、不可流式。

---

## 15. 参考资料

### 15.1 算法 / RFC 标准

- [RFC 5116 - An Interface and Algorithms for Authenticated Encryption](https://www.rfc-editor.org/rfc/rfc5116) — AEAD 接口
- [RFC 8439 - ChaCha20 and Poly1305 for IETF Protocols](https://www.rfc-editor.org/rfc/rfc8439) — ChaCha20-Poly1305 AEAD
- [RFC 5297 - Synthetic Initialization Vector (SIV) Authenticated Encryption Using AES](https://www.rfc-editor.org/rfc/rfc5297) — AES-SIV
- [RFC 9106 - Argon2 Memory-Hard Function](https://www.rfc-editor.org/rfc/rfc9106) — Argon2
- [RFC 7914 - The scrypt Password-Based Key Derivation Function](https://www.rfc-editor.org/rfc/rfc7914) — scrypt
- [RFC 3394 - Advanced Encryption Standard (AES) Key Wrap Algorithm](https://www.rfc-editor.org/rfc/rfc3394) — AES Key Wrap
- [NIST SP 800-38D - Recommendation for Block Cipher Modes of Operation: Galois/Counter Mode (GCM)](https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication800-38d.pdf) — AES-GCM
- [FIPS 197 - Advanced Encryption Standard (AES)](https://nvlpubs.nist.gov/nistpubs/FIPS/NIST.FIPS.197-upd1.pdf) — AES

### 15.2 KDF 指南

- [OWASP Password Storage Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html) — OWASP Argon2id / scrypt / PBKDF2 推荐参数
- [Password Hashing Competition](https://www.password-hashing.net/) — Argon2 比赛官方
- [Argon2 原始论文 - Biryukov, Dinu, Khovratovich (2015)](https://www.cryptolux.org/images/0/0d/Argon2.pdf) — Argon2 算法设计
- [scrypt 原始论文 - Percival (2009)](https://www.tarsnap.com/scrypt/scrypt.pdf) — scrypt 算法

### 15.3 Cryptomator 文档

- [Security Architecture](https://docs.cryptomator.org/en/latest/security/architecture/) — Vault 配置、Master Key、Scrypt 派生
- [Vault Cryptography](https://docs.cryptomator.org/en/latest/security/vault/) — 文件头加密、文件名 AES-SIV、目录 ID 平铺
- [Security Target](https://docs.cryptomator.org/en/latest/security/security-target/) — 威胁模型
- [Cryptomator GitHub](https://github.com/cryptomator/cryptomator)

### 15.4 gocryptfs 文档

- [Cryptography (Forward Mode)](https://nuetzlich.net/gocryptfs/forward_mode_crypto/) — AES-GCM 内容加密、EME 文件名、scrypt KDF
- [File Format](https://github.com/rfjakob/gocryptfs/blob/master/Documentation/file-format.md) — Header / Block 详细布局
- [Threat Model](https://nuetzlich.net/gocryptfs/threat_model/) — Eve / Dragon / Mallory 攻击场景
- [Comparison with Other Projects](https://nuetzlich.net/gocryptfs/comparison/) — 性能基准（AES-GCM 写 482 MiB/s / 读 944 MiB/s）
- [Defuse Security Audit (2017)](https://defuse.ca/audits/gocryptfs.htm) — 第三方审计

### 15.5 CryFS / rclone / 其他

- [CryFS Man Page](https://sources.debian.org/src/cryfs/0.9.10-2/doc/man/cryfs.1/) — aes-256-gcm 默认，blocksize 32 KiB
- [rclone crypt](https://rclone.org/crypt/) — XSalsa20-Poly1305、scrypt、EME 文件名
- [Bitwarden Argon2id](https://bitwarden.com/help/what-encryption-is-used/) — 64 MiB / 3 iter / 4 lanes
- [OWASP Cryptographic Storage Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Cryptographic_Storage_Cheat_Sheet.html)

### 15.6 Apple iCloud 高级数据保护

- [iCloud data security overview](https://support.apple.com/en-us/102651) — 25 类 E2EE 数据
- [Advanced Data Protection for iCloud](https://support.apple.com/en-us/108756) — Recovery Key / Recovery Contact
- [iCloud data security overview (CN)](https://support.apple.com/zh-cn/102651)

### 15.7 Signal 协议

- [Signal X3DH 规范](https://signal.org/docs/specifications/x3dh/) — 初始密钥协商
- [Double Ratchet 规范](https://signal.org/docs/specifications/doubleratchet/) — 每消息新 key
- [Signal Sealed Sender](https://signal.org/blog/sealed-sender/) — 元数据保护

### 15.8 Google Tink

- [Tink GitHub](https://github.com/tink-crypto/tink) — 主仓库（已迁移到 tink-crypto）
- [Tink Python HOWTO](https://github.com/tink-crypto/tink/blob/master/docs/PYTHON-HOWTO.md)
- [Tink Supported Primitives](https://github.com/tink-crypto/tink/blob/master/docs/PRIMITIVES.md)
- [Tink Key Management](https://github.com/tink-crypto/tink/blob/master/docs/KEY-MANAGEMENT.md)
- [Tink Envelope Encryption](https://developers.google.com/tink/client-side-encryption)

### 15.9 内存安全

- [libsodium Secure Memory Documentation](https://doc.libsodium.org/memory_management) — sodium_memzero / sodium_mlock / sodium_malloc
- [cryptography Known Limitations](https://cryptography.io/en/latest/limitations/) — "does not clear memory by default"
- [CERT MEM03-C - Clear sensitive information](https://wiki.sei.cmu.edu/confluence/display/c/MEM03-C.+Clear+sensitive+information+stored+in+reusable+resources) — 内存擦除指南

### 15.10 homeTrove 内部代码（已读）

- `/workspace/hometrove/models.py` — `assets` 表结构
- `/workspace/hometrove/scanner/__init__.py` — 扫描器实现（只读扫描 + commit_batch）
- `/workspace/hometrove/uploads/__init__.py` — 分块上传 + 原子 rename
- `/workspace/hometrove/api/routes/assets.py` — `FileResponse` 明文输出 + `_asset_path` 路径解析
- `/workspace/hometrove/plugins/builtin/thumbnail.py` — 缩略图生成 + PyAV 视频抽帧
- `/workspace/hometrove/api/__init__.py` — FastAPI app 工厂 + read_only_check
- `/workspace/hometrove/auth.py` — PassthroughAuthBackend（单用户）

### 15.11 其他

- [Linux man mlock(2)](https://man7.org/linux/man-pages/man2/mlock.2.html) — mlock 系统调用
- [Linux man madvise(2)](https://man7.org/linux/man-pages/man2/madvise.2.html) — MADV_DONTDUMP（不进 core dump）
- [dm-crypt / LUKS](https://gitlab.com/cryptsetup/cryptsetup) — 卷级加密
- [BitLocker](https://learn.microsoft.com/en-us/windows/security/operating-system-security/data-protection/bitlocker/) — Windows 全盘加密
- [FileVault](https://support.apple.com/en-us/HT204837) — macOS 全盘加密
- [Wycheproof (Google)](https://github.com/google/wycheproof) — 加密库测试向量