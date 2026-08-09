import { useEffect, useRef } from "react";
import { useParams, useSearchParams, Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, mediaLabel, thumbUrl, type SimilarHitDTO, type KeyframeDTO } from "../lib/api";
import { PluginDataBlock, PLUGIN_LABELS } from "../components/kv";

function fmtTs(ts: number | null | undefined): string {
  if (!ts) return "–";
  return new Date(ts * 1000).toLocaleString("zh-CN");
}

function SimilarCard({ hit }: { hit: SimilarHitDTO }) {
  const isImage = hit.media_type === "image";
  return (
    <Link
      to={`/asset/${hit.asset_id}`}
      className="group relative m-[2px] block aspect-[4/3] overflow-hidden rounded-sm bg-neutral-200 dark:bg-neutral-800"
    >
      <img
        src={thumbUrl(hit.asset_id, isImage ? "small" : "placeholder")}
        alt=""
        loading="lazy"
        className="h-full w-full object-cover transition group-hover:scale-105"
        onError={(e) => {
          e.currentTarget.style.display = "none";
        }}
      />
      {!isImage && (
        <span className="absolute inset-0 flex items-center justify-center text-xs font-medium text-white/90">
          <span className="rounded bg-black/50 px-2 py-0.5">{mediaLabel(hit.media_type)}</span>
        </span>
      )}
      <span className="absolute bottom-1 right-1 rounded bg-black/60 px-1 text-[10px] text-white">
        {(hit.distance * 100).toFixed(1)}%
      </span>
    </Link>
  );
}

function formatTime(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

interface SceneInfo {
  start: number;
  end: number;
}

function SceneScrubber({
  scenes,
  duration,
  onSeek,
}: {
  scenes: SceneInfo[];
  duration: number | null;
  onSeek: (t: number) => void;
}) {
  if (!scenes || scenes.length === 0) return null;
  const total = duration ?? scenes[scenes.length - 1].end;
  if (!total || total <= 0) return null;
  return (
    <div className="mt-2 flex h-3 w-full overflow-hidden rounded bg-neutral-200 dark:bg-neutral-800">
      {scenes.map((s, i) => (
        <button
          key={i}
          title={`场景 ${i + 1} · ${formatTime(s.start)}–${formatTime(s.end)}`}
          onClick={() => onSeek(s.start)}
          className="h-full border-r border-white/60 bg-brand-500/70 transition hover:bg-brand-500"
          style={{ width: `${((s.end - s.start) / total) * 100}%` }}
        />
      ))}
    </div>
  );
}

function KeyframeStrip({ assetId, onSeek }: { assetId: number; onSeek: (t: number) => void }) {
  const { data, isFetching } = useQuery({
    queryKey: ["keyframes", assetId],
    queryFn: () => api.keyframes(assetId),
    enabled: Number.isFinite(assetId),
  });
  const items: KeyframeDTO[] = data?.items ?? [];

  if (isFetching) {
    return <p className="px-1 py-2 text-xs text-neutral-400">关键帧加载中…</p>;
  }
  if (items.length === 0) {
    return null;
  }
  return (
    <div className="mt-2 flex gap-2 overflow-x-auto pb-1">
      {items.map((kf) => (
        <button
          key={`${kf.scene}-${kf.index}`}
          onClick={() => onSeek(kf.t_sec)}
          title={`跳转到 ${formatTime(kf.t_sec)}`}
          className="group relative shrink-0 overflow-hidden rounded-md bg-neutral-100 dark:bg-neutral-900"
        >
          <img
            src={kf.url}
            alt={`场景 ${kf.scene} 关键帧 ${kf.index}`}
            loading="lazy"
            className="h-16 w-28 object-cover transition group-hover:scale-105"
          />
          <span className="absolute bottom-0.5 left-0.5 rounded bg-black/60 px-1 text-[10px] text-white">
            {formatTime(kf.t_sec)}
          </span>
        </button>
      ))}
    </div>
  );
}

function SimilarSection({ assetId }: { assetId: number }) {
  const { data, isFetching, isError, refetch } = useQuery({
    queryKey: ["similar", assetId],
    queryFn: () => api.similar(assetId, 24),
    enabled: Number.isFinite(assetId),
  });
  const items: SimilarHitDTO[] = data?.items ?? [];

  if (isFetching) {
    return <p className="text-sm text-neutral-400">正在召回相似资源…</p>;
  }
  if (isError) {
    return (
      <p className="text-sm text-red-500">
        相似搜索失败
        <button onClick={() => refetch()} className="ml-2 underline">
          重试
        </button>
      </p>
    );
  }
  if (items.length === 0) {
    return <p className="text-sm text-neutral-400">暂无相似资源。索引完成后会出现在这里。</p>;
  }
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6">
      {items.map((h) => (
        <SimilarCard key={h.asset_id} hit={h} />
      ))}
    </div>
  );
}

export default function AssetDetail() {
  const { id } = useParams();
  const assetId = Number(id);
  const [searchParams] = useSearchParams();
  const rawT = searchParams.get("t");
  const qc = useQueryClient();
  const videoRef = useRef<HTMLVideoElement>(null);
  // Always include trashed rows so a user can browse a trashed asset's
  // detail from the Trash page; the ``deleted_at`` field on the response
  // decides which action buttons to render.
  const { data: asset, isLoading, isError } = useQuery({
    queryKey: ["asset", assetId],
    queryFn: () => api.asset(assetId, true),
    enabled: Number.isFinite(assetId),
  });

  const moveToTrash = useMutation({
    mutationFn: () => api.moveToTrash(assetId),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["asset", assetId] });
      await qc.invalidateQueries({ queryKey: ["trash"] });
      await qc.invalidateQueries({ queryKey: ["assets"] });
    },
  });

  const restore = useMutation({
    mutationFn: () => api.restoreFromTrash(assetId),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["asset", assetId] });
      await qc.invalidateQueries({ queryKey: ["trash"] });
      await qc.invalidateQueries({ queryKey: ["assets"] });
    },
  });

  const toggleFavorite = useMutation({
    mutationFn: () => api.toggleFavorite(assetId),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["asset", assetId] });
      await qc.invalidateQueries({ queryKey: ["assets"] });
    },
  });

  const seekTo = (t: number) => {
    const v = videoRef.current;
    if (!v) return;
    if (Number.isFinite(t) && t >= 0) {
      v.currentTime = t;
    }
    v.play().catch(() => {});
    v.scrollIntoView({ behavior: "smooth", block: "nearest" });
  };

  const seekT = rawT ? Number(rawT) : NaN;
  useEffect(() => {
    if (!asset || asset.media_type !== "video" || !Number.isFinite(seekT) || seekT < 0) {
      return;
    }
    const v = videoRef.current;
    if (!v) return;
    const apply = () => {
      if (asset.duration_sec == null || seekT < asset.duration_sec) {
        v.currentTime = seekT;
      }
    };
    if (v.readyState >= 1) {
      apply();
    } else {
      v.addEventListener("loadedmetadata", apply, { once: true });
    }
  }, [asset, seekT]);

  if (!Number.isFinite(assetId)) {
    return <p className="p-6 text-neutral-500">无效的资源 ID</p>;
  }
  if (isLoading) {
    return <p className="p-6 text-neutral-400">加载中…</p>;
  }
  if (isError || !asset) {
    return <p className="p-6 text-red-500">加载失败：资源不存在</p>;
  }

  const fileName = asset.path.split("\0").pop() ?? asset.path;
  const pluginEntries = Object.entries(asset.plugin_results ?? {});
  const isTrashed = asset.deleted_at != null;
  const sceneData = asset.plugin_results?.["basic.scene_detect"]?.data;
  const scenes: SceneInfo[] = Array.isArray(sceneData?.scenes)
    ? (sceneData.scenes as SceneInfo[])
    : [];

  return (
    <div className="p-4 md:p-6">
      <Link to={isTrashed ? "/trash" : "/timeline"} className="text-sm text-brand-500 hover:underline">
        ← 返回{isTrashed ? "回收站" : "时间轴"}
      </Link>

      <div className="mt-3 flex flex-col gap-4 md:flex-row">
        <div className="shrink-0 overflow-hidden rounded-md bg-neutral-100 dark:bg-neutral-900 md:w-96">
          {asset.media_type === "image" ? (
            <img
              src={`/api/assets/${asset.id}/file`}
              alt={fileName}
              className="h-auto w-full object-contain"
            />
          ) : asset.media_type === "video" ? (
            <>
              <video
                ref={videoRef}
                src={`/api/assets/${asset.id}/file`}
                controls
                className="w-full"
              />
              {!isTrashed && scenes.length > 0 && (
                <SceneScrubber
                  scenes={scenes}
                  duration={asset.duration_sec}
                  onSeek={seekTo}
                />
              )}
              {!isTrashed && (
                <KeyframeStrip
                  assetId={asset.id}
                  onSeek={seekTo}
                />
              )}
            </>
          ) : (
            <div className="flex h-48 items-center justify-center text-neutral-400">
              {mediaLabel(asset.media_type)}
            </div>
          )}
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-3">
            <h1 className="break-all text-lg font-semibold">{fileName}</h1>
            {isTrashed && (
              <span className="rounded bg-red-100 px-2 py-0.5 text-xs font-medium text-red-700 dark:bg-red-500/20 dark:text-red-300">
                回收站 · {fmtTs(asset.deleted_at)}
              </span>
            )}
          </div>
          <p className="mt-1 text-sm text-neutral-500">
            {mediaLabel(asset.media_type)}
            {asset.size_bytes != null && ` · ${(asset.size_bytes / 1024).toFixed(0)} KB`}
            {asset.width && asset.height
              ? ` · ${asset.width}×${asset.height}`
              : ""}
            {asset.duration_sec != null
              ? ` · ${asset.duration_sec.toFixed(1)}s`
              : ""}
          </p>

          <div className="mt-4 flex flex-wrap gap-2">
            {isTrashed ? (
              <button
                onClick={() => restore.mutate()}
                disabled={restore.isPending}
                className="rounded-md bg-brand-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-600 disabled:opacity-50"
              >
                {restore.isPending ? "还原中…" : "从回收站还原"}
              </button>
            ) : (
              <>
                <button
                  onClick={() => toggleFavorite.mutate()}
                  disabled={toggleFavorite.isPending}
                  className={`rounded-md border px-3 py-1.5 text-sm disabled:opacity-50 ${
                    asset.favorite
                      ? "border-yellow-400 bg-yellow-50 text-yellow-700 dark:bg-yellow-500/10 dark:text-yellow-300"
                      : "border-neutral-300 text-neutral-600 hover:bg-neutral-100 dark:border-neutral-700 dark:text-neutral-300 dark:hover:bg-neutral-800"
                  }`}
                >
                  {asset.favorite ? "★ 已收藏" : "☆ 收藏"}
                </button>
                <button
                  onClick={() => {
                    if (confirm("移到回收站？文件本身不会被删除，30 天后该索引记录会自动清理。")) {
                      moveToTrash.mutate();
                    }
                  }}
                  disabled={moveToTrash.isPending}
                  className="rounded-md border border-red-300 px-3 py-1.5 text-sm text-red-600 hover:bg-red-50 disabled:opacity-50 dark:border-red-700 dark:text-red-400 dark:hover:bg-red-500/10"
                >
                  {moveToTrash.isPending ? "处理中…" : "移到回收站"}
                </button>
              </>
            )}
          </div>

          <div className="mt-6 grid gap-4">
            {!isTrashed && (
              <section className="rounded-md border border-neutral-200 dark:border-neutral-800">
                <header className="flex items-center justify-between border-b border-neutral-100 px-4 py-2 dark:border-neutral-800">
                  <h2 className="text-sm font-medium">找相似</h2>
                  <span className="text-xs text-neutral-400">基于向量近邻召回</span>
                </header>
                <div className="px-4 py-3">
                  <SimilarSection assetId={asset.id} />
                </div>
              </section>
            )}

            {pluginEntries.length === 0 && (
              <p className="text-sm text-neutral-400">
                暂无插件分析数据。文件会由后台 worker 自动索引。
              </p>
            )}
            {pluginEntries.map(([pluginId, pr]) => (
              <section
                key={pluginId}
                className="rounded-md border border-neutral-200 dark:border-neutral-800"
              >
                <header className="flex items-center justify-between border-b border-neutral-100 px-4 py-2 dark:border-neutral-800">
                  <h2 className="text-sm font-medium">
                    {PLUGIN_LABELS[pluginId] ?? pluginId}
                  </h2>
                  <span
                    className={`rounded px-1.5 py-0.5 text-xs ${
                      pr.status === "ok"
                        ? "bg-green-100 text-green-700 dark:bg-green-500/20 dark:text-green-300"
                        : "bg-red-100 text-red-700 dark:bg-red-500/20 dark:text-red-300"
                    }`}
                  >
                    {pr.status === "ok" ? "完成" : pr.status}
                  </span>
                </header>
                <div className="px-4 py-2">
                  <PluginDataBlock pluginId={pluginId} data={pr.data} />
                </div>
                <footer className="border-t border-neutral-100 px-4 py-1.5 text-xs text-neutral-400 dark:border-neutral-800">
                  v{pr.version}
                  {pr.elapsed_ms != null && ` · ${pr.elapsed_ms}ms`}
                  {pr.finished_at != null && ` · ${fmtTs(pr.finished_at)}`}
                </footer>
              </section>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
