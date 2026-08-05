import { useEffect, useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, mediaLabel, thumbUrl, type AssetDTO } from "../lib/api";

function Thumb({ asset, onClick }: { asset: AssetDTO; onClick: (a: AssetDTO) => void }) {
  const isImage = asset.media_type === "image";
  return (
    <button
      onClick={() => onClick(asset)}
      className="group relative m-[2px] aspect-[4/3] overflow-hidden rounded-sm bg-neutral-200 dark:bg-neutral-800"
    >
      <img
        src={thumbUrl(asset.id, isImage ? "small" : "placeholder")}
        alt=""
        loading="lazy"
        className="h-full w-full object-cover transition group-hover:scale-105"
        onError={(e) => {
          const el = e.currentTarget;
          if (!isImage && el.src.includes("placeholder")) {
            el.style.display = "none";
            return;
          }
          el.style.display = "none";
        }}
      />
      {!isImage && (
        <span className="absolute inset-0 flex items-center justify-center text-xs font-medium text-white/90">
          <span className="rounded bg-black/50 px-2 py-0.5">{mediaLabel(asset.media_type)}</span>
        </span>
      )}
      {asset.media_type === "video" && asset.duration_sec ? (
        <span className="absolute bottom-1 right-1 rounded bg-black/60 px-1 text-[10px] text-white">
          {Math.round(asset.duration_sec)}s
        </span>
      ) : null}
    </button>
  );
}

function Lightbox({ asset, onClose }: { asset: AssetDTO; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 p-4"
      onClick={onClose}
    >
      <div
        className="max-h-full max-w-full overflow-auto"
        onClick={(e) => e.stopPropagation()}
      >
        {asset.media_type === "image" ? (
          <img
            src={`/api/assets/${asset.id}/file`}
            alt={asset.path}
            className="max-h-[90vh] max-w-full object-contain"
          />
        ) : (
          <video src={`/api/assets/${asset.id}/file`} controls autoPlay className="max-h-[90vh] max-w-full" />
        )}
      </div>
      <button
        onClick={onClose}
        className="absolute right-4 top-4 rounded-full bg-white/10 px-3 py-1.5 text-sm text-white hover:bg-white/20"
      >
        ✕
      </button>
      <div className="absolute bottom-4 left-1/2 -translate-x-1/2 truncate text-xs text-white/70">
        {asset.path.split("\0").pop()}
      </div>
      <Link
        to={`/asset/${asset.id}`}
        className="absolute right-4 top-4 rounded-full bg-white/10 px-3 py-1.5 text-sm text-white hover:bg-white/20"
        onClick={(e) => e.stopPropagation()}
      >
        详情
      </Link>
    </div>
  );
}

export default function Timeline() {
  const [mediaType, setMediaType] = useState<string | undefined>();
  const [preview, setPreview] = useState<AssetDTO | null>(null);

  const {
    data,
    fetchNextPage,
    hasNextPage,
    isFetching,
    isFetchingNextPage,
  } = useInfiniteQuery({
    queryKey: ["assets", mediaType],
    queryFn: ({ pageParam }) => api.assets(pageParam, mediaType),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });

  useEffect(() => {
    const onScroll = () => {
      if (
        window.innerHeight + window.scrollY >=
        document.body.offsetHeight - 800
      ) {
        if (hasNextPage && !isFetchingNextPage) fetchNextPage();
      }
    };
    window.addEventListener("scroll", onScroll);
    return () => window.removeEventListener("scroll", onScroll);
  }, [hasNextPage, isFetchingNextPage, fetchNextPage]);

  const items = data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <div className="p-4 md:p-6">
      <div className="mb-4 flex items-center gap-2">
        <h1 className="text-xl font-semibold">时间轴</h1>
        <div className="ml-auto flex gap-1">
          {["image", "video"].map((t) => (
            <button
              key={t}
              onClick={() => setMediaType(mediaType === t ? undefined : t)}
              className={`rounded-md px-3 py-1 text-xs transition ${
                mediaType === t
                  ? "bg-brand-500 text-white"
                  : "bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300"
              }`}
            >
              {mediaLabel(t)}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6">
        {items.map((a) => (
          <Thumb key={a.id} asset={a} onClick={setPreview} />
        ))}
      </div>

      {items.length === 0 && !isFetching && (
        <p className="mt-10 text-center text-neutral-400">
          还没有媒体文件。点击左上「立即扫描媒体目录」，或到「上传」页上传。
        </p>
      )}

      {isFetchingNextPage && (
        <p className="mt-4 text-center text-sm text-neutral-400">加载更多…</p>
      )}

      {preview && <Lightbox asset={preview} onClose={() => setPreview(null)} />}
    </div>
  );
}
