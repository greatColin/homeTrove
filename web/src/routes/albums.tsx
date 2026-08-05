import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api, mediaLabel, thumbUrl, type AlbumDTO } from "../lib/api";

function fmtDate(ts: number | null | undefined): string {
  if (!ts) return "–";
  return new Date(ts * 1000).toLocaleDateString("zh-CN");
}

function AssetThumb({ assetId, video }: { assetId: number; video?: boolean }) {
  return (
    <div className="relative m-[2px] aspect-[4/3] overflow-hidden rounded-sm bg-neutral-200 dark:bg-neutral-800">
      <img
        src={thumbUrl(assetId, video ? "placeholder" : "small")}
        alt=""
        loading="lazy"
        className="h-full w-full object-cover"
        onError={(e) => {
          (e.currentTarget as HTMLImageElement).style.display = "none";
        }}
      />
    </div>
  );
}

function CreateAlbumForm({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const qc = useQueryClient();
  const create = useMutation({
    mutationFn: () => api.createAlbum(name, desc),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["albums"] });
      onDone();
    },
  });
  return (
    <form
      className="mt-4 flex flex-col gap-2 rounded-lg border border-neutral-200 p-4 dark:border-neutral-700"
      onSubmit={(e) => {
        e.preventDefault();
        if (name.trim()) create.mutate();
      }}
    >
      <h3 className="font-medium">新建相册</h3>
      <input
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="相册名称"
        className="rounded border border-neutral-300 px-2 py-1 text-sm dark:border-neutral-600 dark:bg-neutral-800"
      />
      <input
        value={desc}
        onChange={(e) => setDesc(e.target.value)}
        placeholder="描述（可选）"
        className="rounded border border-neutral-300 px-2 py-1 text-sm dark:border-neutral-600 dark:bg-neutral-800"
      />
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={!name.trim() || create.isPending}
          className="rounded bg-brand-500 px-3 py-1 text-sm font-medium text-white hover:bg-brand-600 disabled:opacity-50"
        >
          {create.isPending ? "创建中…" : "创建"}
        </button>
        <button
          type="button"
          onClick={onDone}
          className="rounded border border-neutral-300 px-3 py-1 text-sm dark:border-neutral-600"
        >
          取消
        </button>
      </div>
      {create.isError && (
        <p className="text-sm text-red-500">创建失败：{(create.error as Error).message}</p>
      )}
    </form>
  );
}

function AlbumCard({ album }: { album: AlbumDTO }) {
  const cover = album.cover_asset_id ?? album.asset_ids[0];
  return (
    <Link
      to={`/albums/${album.id}`}
      className="group block overflow-hidden rounded-lg border border-neutral-200 bg-white shadow-sm transition hover:shadow-md dark:border-neutral-700 dark:bg-neutral-900"
    >
      <div className="relative h-36 bg-neutral-100 dark:bg-neutral-800">
        {cover != null ? (
          <AssetThumb assetId={cover} />
        ) : (
          <div className="flex h-full items-center justify-center text-neutral-400">
            空相册
          </div>
        )}
        <span className="absolute right-2 top-2 rounded bg-black/50 px-1.5 py-0.5 text-xs text-white">
          {album.asset_count} 项
        </span>
      </div>
      <div className="p-3">
        <p className="truncate font-medium">{album.name}</p>
        {album.description && (
          <p className="mt-0.5 truncate text-sm text-neutral-500">{album.description}</p>
        )}
        <p className="mt-1 text-xs text-neutral-400">{fmtDate(album.updated_at)}</p>
      </div>
    </Link>
  );
}

function AlbumList() {
  const [creating, setCreating] = useState(false);
  const { data } = useQuery({ queryKey: ["albums"], queryFn: api.albums });
  const albums = data?.items ?? [];
  return (
    <div className="p-4 md:p-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">相册</h1>
        <button
          onClick={() => setCreating((v) => !v)}
          className="rounded bg-brand-500 px-3 py-1 text-sm font-medium text-white hover:bg-brand-600"
        >
          {creating ? "取消" : "新建相册"}
        </button>
      </div>
      {creating && <CreateAlbumForm onDone={() => setCreating(false)} />}
      {albums.length === 0 ? (
        <p className="mt-6 text-sm text-neutral-500">
          还没有相册。点击「新建相册」创建，之后可在资源详情或相册内添加照片。
        </p>
      ) : (
        <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {albums.map((a) => (
            <AlbumCard key={a.id} album={a} />
          ))}
        </div>
      )}
    </div>
  );
}

function AlbumDetail() {
  const { id } = useParams();
  const albumId = Number(id);
  const qc = useQueryClient();
  const [showAdd, setShowAdd] = useState(false);
  const [rename, setRename] = useState("");

  const { data: album, isLoading, isError } = useQuery({
    queryKey: ["album", albumId],
    queryFn: () => api.album(albumId),
    enabled: Number.isFinite(albumId),
  });
  const { data: allAssets } = useQuery({
    queryKey: ["assets", 0],
    queryFn: () => api.assets(undefined),
    enabled: showAdd,
  });
  const [picked, setPicked] = useState<number[]>([]);

  const invalidate = () => qc.invalidateQueries({ queryKey: ["album", albumId] });

  const add = useMutation({
    mutationFn: (ids: number[]) => api.addToAlbum(albumId, ids),
    onSuccess: () => {
      invalidate();
      setPicked([]);
      setShowAdd(false);
    },
  });
  const remove = useMutation({
    mutationFn: (ids: number[]) => api.removeFromAlbum(albumId, ids),
    onSuccess: invalidate,
  });
  const setCover = useMutation({
    mutationFn: (assetId: number) => api.updateAlbum(albumId, { cover_asset_id: assetId }),
    onSuccess: invalidate,
  });
  const renameMut = useMutation({
    mutationFn: () => api.updateAlbum(albumId, { name: rename }),
    onSuccess: invalidate,
  });
  const del = useMutation({
    mutationFn: () => api.deleteAlbum(albumId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["albums"] }),
  });

  if (!Number.isFinite(albumId)) return <p className="p-6 text-neutral-500">无效相册 ID</p>;
  if (isLoading) return <p className="p-6 text-neutral-400">加载中…</p>;
  if (isError || !album) return <p className="p-6 text-red-500">相册不存在</p>;

  const inAlbum = new Set(album.asset_ids);
  const candidateAssets = (allAssets?.items ?? []).filter((a) => !inAlbum.has(a.id));
  const isRenaming = rename.trim().length > 0 && rename !== album.name;

  return (
    <div className="p-4 md:p-6">
      <Link to="/albums" className="text-sm text-brand-500 hover:underline">
        ← 返回相册
      </Link>

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">{album.name}</h1>
        <span className="text-sm text-neutral-500">{album.asset_count} 项</span>
        <button
          onClick={() => del.mutate()}
          className="ml-auto rounded border border-red-300 px-2 py-1 text-sm text-red-600 hover:bg-red-50 dark:border-red-800 dark:hover:bg-red-950"
        >
          删除相册
        </button>
      </div>
      {album.description && <p className="mt-1 text-sm text-neutral-500">{album.description}</p>}

      <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
        <button
          onClick={() => setShowAdd((v) => !v)}
          className="rounded bg-brand-500 px-3 py-1 font-medium text-white hover:bg-brand-600"
        >
          {showAdd ? "取消" : "添加照片"}
        </button>
        {rename.trim() && isRenaming && (
          <button
            onClick={() => renameMut.mutate()}
            className="rounded border border-neutral-300 px-2 py-1 dark:border-neutral-600"
          >
            保存新名称
          </button>
        )}
      </div>

      {showAdd && (
        <div className="mt-3 rounded-lg border border-neutral-200 p-3 dark:border-neutral-700">
          <div className="flex items-center justify-between">
            <p className="text-sm text-neutral-500">
              选择要加入的资产（已选 {picked.length}）
            </p>
            <button
              onClick={() => add.mutate(picked)}
              disabled={picked.length === 0 || add.isPending}
              className="rounded bg-brand-500 px-3 py-1 text-sm font-medium text-white hover:bg-brand-600 disabled:opacity-50"
            >
              {add.isPending ? "添加中…" : "添加所选"}
            </button>
          </div>
          {candidateAssets.length === 0 ? (
            <p className="mt-2 text-sm text-neutral-500">所有资产都已在此相册中。</p>
          ) : (
            <div className="mt-2 grid grid-cols-3 gap-1 sm:grid-cols-4 lg:grid-cols-6">
              {candidateAssets.map((a) => {
                const sel = picked.includes(a.id);
                return (
                  <button
                    key={a.id}
                    onClick={() =>
                      setPicked((p) => (sel ? p.filter((x) => x !== a.id) : [...p, a.id]))
                    }
                    className={`relative overflow-hidden rounded-sm border-2 ${
                      sel
                        ? "border-brand-500"
                        : "border-transparent hover:border-neutral-300 dark:hover:border-neutral-600"
                    }`}
                  >
                    <AssetThumb assetId={a.id} video={a.media_type === "video"} />
                    {sel && (
                      <span className="absolute right-1 top-1 rounded bg-brand-500 px-1 text-xs text-white">
                        ✓
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      )}

      <input
        value={rename}
        onChange={(e) => setRename(e.target.value)}
        placeholder={rename ? "输入新名称后保存" : "重命名相册（回车保存）"}
        onKeyDown={(e) => {
          if (e.key === "Enter" && isRenaming) renameMut.mutate();
        }}
        className="mt-3 w-full max-w-xs rounded border border-neutral-300 px-2 py-1 text-sm dark:border-neutral-600 dark:bg-neutral-800"
      />

      {album.asset_ids.length === 0 ? (
        <p className="mt-6 text-sm text-neutral-500">空相册。点击「添加照片」开始整理。</p>
      ) : (
        <div className="mt-3 grid grid-cols-3 gap-1 sm:grid-cols-4 lg:grid-cols-6 xl:grid-cols-8">
          {album.asset_ids.map((aid) => (
            <div key={aid} className="group relative">
              <Link to={`/asset/${aid}`}>
                <AssetThumb assetId={aid} />
              </Link>
              <div className="absolute inset-0 hidden items-center justify-center gap-2 bg-black/40 group-hover:flex">
                <button
                  onClick={() => setCover.mutate(aid)}
                  title="设为封面"
                  className="rounded bg-white/90 px-1.5 py-0.5 text-xs text-black"
                >
                  封面
                </button>
                <button
                  onClick={() => remove.mutate([aid])}
                  title="移出相册"
                  className="rounded bg-red-500/90 px-1.5 py-0.5 text-xs text-white"
                >
                  移除
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
      {remove.isError && <p className="mt-2 text-sm text-red-500">操作失败</p>}
    </div>
  );
}

export default function Albums() {
  const { id } = useParams();
  return id != null ? <AlbumDetail /> : <AlbumList />;
}
