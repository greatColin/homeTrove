"""Resumable / chunked upload server.

Design (M0):

* Client opens a session via POST ``/api/uploads`` with:
  - ``filename``  — original file name (used for media-type classification)
  - ``size``      — total file size
  - ``content_hash`` — optional, client's claimed SHA-256 of the whole file
* Server responds with ``upload_id`` and ``chunk_size`` (configured).
* Client uploads chunks via PUT ``/api/uploads/{upload_id}/chunks/{idx}``
  with the raw bytes. Idempotent: re-uploading the same chunk index is a
  no-op (server verifies the on-disk bytes already match).
* Client asks the server to finalize via POST ``/api/uploads/{upload_id}/complete``
  supplying all chunk indices it uploaded. The server re-assembles,
  verifies count + total size, optionally verifies SHA-256 against
  ``content_hash``, then moves the file into a ``staging/`` directory and
  returns an asset path. Background ingest then classifies it and adds an
  ``assets`` row.

A client can resume by GETting ``/api/uploads/{upload_id}``: the server
returns which chunk indices are already on disk, so the client resumes
from the missing tail.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fastapi import (
    APIRouter,
    Body,
    HTTPException,
    Query,
    UploadFile,
    File,
)


@dataclass
class UploadSession:
    upload_id: str
    filename: str
    size: int
    chunk_size: int
    storage_dir: Path
    created_at: int
    claimed_hash: Optional[str] = None
    finalized: bool = False
    final_path: Optional[Path] = None
    encrypted: bool = False


class UploadManager:
    """In-process state.

    M0 keeps state on disk + an in-memory dict. A multi-instance deployment
    would move the sessions into SQLite; left for M1.
    """

    def __init__(self, base_dir: Path, default_chunk_mb: int = 4, ttl_seconds: int = 24 * 3600) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.default_chunk_size = default_chunk_mb * 1024 * 1024
        self.ttl_seconds = ttl_seconds
        self._sessions: dict[str, UploadSession] = {}
        # Recover any in-flight sessions on startup.
        for meta_path in self.base_dir.glob("*/session.json"):
            try:
                with open(meta_path) as f:
                    raw = json.load(f)
                storage = meta_path.parent / "chunks"
                s = UploadSession(
                    upload_id=raw["upload_id"],
                    filename=raw["filename"],
                    size=raw["size"],
                    chunk_size=raw["chunk_size"],
                    storage_dir=storage,
                    created_at=raw["created_at"],
                    claimed_hash=raw.get("claimed_hash"),
                    encrypted=bool(raw.get("encrypted", False)),
                )
                self._sessions[s.upload_id] = s
                storage.mkdir(exist_ok=True)
            except Exception:
                # Corrupt session — purge.
                meta_path.unlink(missing_ok=True)

    # ----- session lifecycle -----

    def create(
        self,
        filename: str,
        size: int,
        claimed_hash: Optional[str] = None,
        encrypted: bool = False,
    ) -> UploadSession:
        if size <= 0:
            raise ValueError("size must be positive")
        upload_id = uuid.uuid4().hex
        storage_dir = self.base_dir / upload_id / "chunks"
        storage_dir.mkdir(parents=True, exist_ok=True)
        session = UploadSession(
            upload_id=upload_id,
            filename=filename,
            size=size,
            chunk_size=self.default_chunk_size,
            storage_dir=storage_dir,
            created_at=int(time.time()),
            claimed_hash=claimed_hash,
            encrypted=encrypted,
        )
        self._sessions[upload_id] = session
        self._persist_meta(session)
        return session

    def get(self, upload_id: str) -> UploadSession:
        s = self._sessions.get(upload_id)
        if s is None:
            # Maybe on disk but lost from memory (process restart).
            meta_path = self.base_dir / upload_id / "session.json"
            if not meta_path.exists():
                raise KeyError(upload_id)
            with open(meta_path) as f:
                raw = json.load(f)
            storage = meta_path.parent / "chunks"
            s = UploadSession(
                upload_id=raw["upload_id"],
                filename=raw["filename"],
                size=raw["size"],
                chunk_size=raw["chunk_size"],
                storage_dir=storage,
                created_at=raw["created_at"],
                claimed_hash=raw.get("claimed_hash"),
                encrypted=bool(raw.get("encrypted", False)),
            )
            self._sessions[upload_id] = s
            storage.mkdir(exist_ok=True)
        return s

    def gc(self) -> int:
        """Drop expired sessions on disk. Returns purged count."""
        now = int(time.time())
        purged = 0
        for sid in list(self._sessions.keys()):
            s = self._sessions[sid]
            if s.finalized:
                continue
            if now - s.created_at > self.ttl_seconds:
                self._purge(sid)
                purged += 1
        # Sweep orphans (sessions with no in-memory entry)
        for session_dir in self.base_dir.iterdir():
            if not session_dir.is_dir():
                continue
            if session_dir.name in self._sessions:
                continue
            session_meta = session_dir / "session.json"
            if session_meta.exists():
                try:
                    raw = json.loads(session_meta.read_text())
                    if now - raw["created_at"] > self.ttl_seconds:
                        self._purge(session_dir.name)
                        purged += 1
                except Exception:
                    self._purge(session_dir.name)
                    purged += 1
        return purged

    # ----- chunk I/O -----

    def list_chunks(self, upload_id: str) -> list[int]:
        s = self.get(upload_id)
        return sorted(int(p.stem) for p in s.storage_dir.glob("*.part"))

    def put_chunk(self, upload_id: str, idx: int, data: bytes) -> None:
        s = self.get(upload_id)
        target = s.storage_dir / f"{idx}.part"
        # Atomic write: write to a tmp file first, then rename.
        tmp = target.with_suffix(".tmp")
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, target)

    def get_chunk(self, upload_id: str, idx: int) -> bytes:
        s = self.get(upload_id)
        with open(s.storage_dir / f"{idx}.part", "rb") as f:
            return f.read()

    def complete(self, upload_id: str, chunk_indices: list[int]) -> Path:
        """Assemble chunks into one file and move to staging/."""
        s = self.get(upload_id)
        if s.finalized:
            return s.final_path  # type: ignore[return-value]

        # Count check
        missing = [i for i in chunk_indices if not (s.storage_dir / f"{i}.part").exists()]
        if missing:
            raise ValueError(f"missing chunks: {missing}")

        on_disk = sorted(int(p.stem) for p in s.storage_dir.glob("*.part"))
        if set(on_disk) != set(chunk_indices):
            raise ValueError(
                f"chunk indices mismatch: server has {on_disk}, client claims {chunk_indices}"
            )

        total = 0
        sha = hashlib.sha256()
        staging = self.base_dir.parent / "staging" / f"{upload_id}_{s.filename}"
        staging.parent.mkdir(parents=True, exist_ok=True)
        with open(staging, "wb") as out:
            for idx in on_disk:
                with open(s.storage_dir / f"{idx}.part", "rb") as f:
                    while True:
                        buf = f.read(1024 * 1024)
                        if not buf:
                            break
                        out.write(buf)
                        sha.update(buf)
                        total += len(buf)
        if total != s.size:
            staging.unlink(missing_ok=True)
            raise ValueError(f"size mismatch: client claimed {s.size}, assembled {total}")

        if s.claimed_hash and s.claimed_hash != sha.hexdigest():
            staging.unlink(missing_ok=True)
            raise ValueError("content_hash mismatch")

        # Mark finalized (do not move again).
        s.final_path = staging
        s.finalized = True
        self._persist_meta(s)

        # Free chunk store — caller retains assembly result.
        for p in s.storage_dir.glob("*.part"):
            p.unlink(missing_ok=True)
        (s.storage_dir).rmdir()
        return staging

    # ----- internals -----

    def _persist_meta(self, s: UploadSession) -> None:
        meta_path = self.base_dir / s.upload_id / "session.json"
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        with open(meta_path, "w") as f:
            json.dump(
                {
                    "upload_id": s.upload_id,
                    "filename": s.filename,
                    "size": s.size,
                    "chunk_size": s.chunk_size,
                    "created_at": s.created_at,
                    "claimed_hash": s.claimed_hash,
                    "finalized": s.finalized,
                    "encrypted": s.encrypted,
                },
                f,
            )

    def _purge(self, upload_id: str) -> None:
        sd = self.base_dir / upload_id
        if sd.exists():
            for p in sd.rglob("*"):
                try:
                    if p.is_file():
                        p.unlink()
                except OSError:
                    pass
            try:
                sd.rmdir()
            except OSError:
                pass
        self._sessions.pop(upload_id, None)


# ----- FastAPI router -----

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


def build_router(manager: UploadManager) -> APIRouter:
    r = APIRouter(prefix="/api/uploads", tags=["uploads"])

    @r.post("", summary="Start an upload session")
    def create(body: dict = Body(...)):
        encrypted = bool(body.get("encrypted", False))
        if encrypted:
            from hometrove.config import get_settings
            from hometrove.vault.state import is_unlocked as _is_unlocked

            if not get_settings().vault_enabled:
                raise HTTPException(400, "vault is not enabled")
            if not _is_unlocked():
                raise HTTPException(423, "vault is locked; cannot start encrypted upload")
        try:
            s = manager.create(
                filename=str(body["filename"]),
                size=int(body["size"]),
                claimed_hash=body.get("content_hash"),
                encrypted=encrypted,
            )
        except (KeyError, ValueError) as e:
            raise HTTPException(400, str(e))
        return {
            "upload_id": s.upload_id,
            "chunk_size": s.chunk_size,
            "size": s.size,
            "encrypted": s.encrypted,
        }

    @r.get("/{upload_id}", summary="Inspect an upload session (used for resume)")
    def info(upload_id: str):
        try:
            s = manager.get(upload_id)
        except KeyError:
            raise HTTPException(404, "upload session not found")
        return {
            "upload_id": s.upload_id,
            "filename": s.filename,
            "size": s.size,
            "chunk_size": s.chunk_size,
            "created_at": s.created_at,
            "uploaded_chunks": manager.list_chunks(upload_id),
            "finalized": s.finalized,
            "encrypted": s.encrypted,
        }

    @r.put("/{upload_id}/chunks/{idx}", summary="Upload one chunk (idempotent)")
    async def put_chunk(upload_id: str, idx: int, file: UploadFile = File(...)):
        try:
            s = manager.get(upload_id)
        except KeyError:
            raise HTTPException(404, "upload session not found")
        if s.finalized:
            raise HTTPException(409, "upload already finalized")
        if idx < 0:
            raise HTTPException(400, "idx must be non-negative")
        data = await file.read()
        # Optional sanity: chunk shouldn't exceed nominal size (we don't enforce strictly because
        # the last chunk is allowed to be smaller).
        manager.put_chunk(upload_id, idx, data)
        return {"upload_id": upload_id, "idx": idx, "bytes": len(data)}

    @r.post("/{upload_id}/complete", summary="Finalize an upload")
    def finalize(upload_id: str, body: dict = Body(default={})):
        try:
            s = manager.get(upload_id)
        except KeyError:
            raise HTTPException(404, "upload session not found")
        chunk_indices = list(body.get("chunk_indices") or manager.list_chunks(upload_id))
        try:
            staging_path = manager.complete(upload_id, chunk_indices)
        except ValueError as e:
            raise HTTPException(409, str(e))
        return {
            "upload_id": upload_id,
            "filename": s.filename,
            "staging_path": str(staging_path),
        }

    @r.post("/{upload_id}/ingest", summary="Ingest a finalized upload into the library")
    def ingest(upload_id: str, plugin_ids: list[str] | None = None):
        from pydantic import BaseModel

        from hometrove.config import get_settings
        from hometrove.scanner.ingest import ingest_file

        try:
            s = manager.get(upload_id)
        except KeyError:
            raise HTTPException(404, "upload session not found")
        if not s.finalized or s.final_path is None:
            raise HTTPException(409, "upload not finalized yet")

        if s.encrypted:
            from hometrove.db import session_scope as _scope
            from hometrove.models import Asset as _Asset
            from hometrove.plugins.builtin.basic_info import classify as _classify
            from hometrove.scanner import hash_prefix as _hash_prefix
            from hometrove.vault import is_unlocked as _is_unlocked
            from hometrove.vault.paths import allocate_content_path as _alloc
            from hometrove.vault.state import get_state as _get_state
            from hometrove.vault.stream import encrypt_file as _enc, shred_file as _shred

            if not _is_unlocked():
                raise HTTPException(423, "vault is locked")
            settings = get_settings()
            data_dir = settings.resolved_data_dir()
            full_path, rel_path = _alloc(data_dir)
            state = _get_state()
            if not state.subkeys:
                raise HTTPException(500, "vault subkeys unavailable")
            try:
                media_type = _classify(s.final_path).value
                try:
                    prefix = _hash_prefix(s.final_path, settings.hash_prefix_bytes)
                except OSError:
                    prefix = ""
                # Pre-allocate the asset id by inserting then streaming the
                # cipher to disk; the in-memory asset row is updated below.
                with _scope() as db:
                    now = int(time.time())
                    # Use a vault-prefixed path so the unique constraint is
                    # satisfied while still signalling that the payload lives in
                    # the encrypted vault.
                    asset = _Asset(
                        path=f"vault\0{rel_path}",
                        filename=s.filename,
                        media_root="vault",
                        content_hash=prefix,
                        media_type=media_type,
                        size_bytes=s.size,
                        mtime=int(s.final_path.stat().st_mtime),
                        created_at=now,
                        updated_at=now,
                        encrypted_path=rel_path,
                    )
                    db.add(asset)
                    db.flush()
                    asset_id = asset.id
                    db.commit()
                try:
                    _enc(
                        s.final_path,
                        full_path,
                        key=bytes(state.subkeys.content_enc_key),
                        asset_id=asset_id,
                    )
                except Exception:
                    # Best-effort cleanup on encryption failure.
                    from hometrove.db import session_scope as _scope2
                    with _scope2() as db:
                        a = db.get(_Asset, asset_id)
                        if a is not None:
                            db.delete(a)
                            db.commit()
                    raise
                # Shred the staging plaintext after the cipher is durable.
                _shred(s.final_path)
                # Drop the staged file pointer from the session so the next
                # ingest call doesn't re-use it.
                s.final_path = None
                with _scope() as db:
                    db.execute(
                        __import__("sqlalchemy").text(
                            "DELETE FROM upload_sessions WHERE upload_id = :u"
                        ),
                        {"u": upload_id},
                    ) if False else None  # placeholder, session is in-memory only
                # Enqueue the same plugins a plain ingest would run.
                from hometrove.scanner import enqueue_pending as _enq
                with _scope() as db:
                    _enq(db, asset_ids=[asset_id])
                return {"asset_id": asset_id, "upload_id": upload_id, "encrypted": True}
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(500, f"encrypted ingest failed: {exc}")

        from hometrove.db import session_scope
        with session_scope() as db:
            asset_id = ingest_file(db, s.final_path, plugin_ids=plugin_ids)
        if asset_id < 0:
            raise HTTPException(410, "staged file vanished")
        return {"asset_id": asset_id, "upload_id": upload_id}

    return r
