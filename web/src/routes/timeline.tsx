import { useEffect, useState } from "react";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, mediaLabel, thumbUrl, type AssetDTO } from "../lib/api";
import { BulkBar, useSelection } from "../components/bulk_actions";
import { JustifiedGrid, type LayoutItem } from "../components/justified_grid";

interface CellProps {
  item: LayoutItem;
  asset: AssetDTO;
  selected: boolean;
  selectionMode: boolean;
  onClick: (a: AssetDTO) => void;
  onToggleFavorite: (a: AssetDTO) => void;
}

function GridCell({ item, asset, selected, selectionMode, onClick, onToggleFavorite }: CellProps) {
  const isImage = asset.media_type === "image";
  const style: React.CSSProperties = {
    position: "absolute",
    left: item.left,
    top: item.top,
    width: item.width,
    height: item.height,
  };
  return (
    <div
      style={style}
      onClick={() => {
        if (selectionMode) onClick(asset);
      }}
      className={`group cursor-pointer overflow-hidden rounded-sm bg-neutral-200 dark:bg-neutral-800 ${
        selected ? "outline outline-2 outline-brand-500" : ""
      }`}
    >
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onClick(asset);
        }}
        className="block h-full w-full"
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
      </button>
      {!isImage && (
        <span className="pointer-events-none absolute inset-0 flex items-center justify-center text-xs font-medium text-white/90">
          <span className="rounded bg-black/50 px-2 py-0.5">{mediaLabel(asset.media_type)}</span>
        </span>
      )}
      {asset.media_type === "video" && asset.duration_sec ? (
        <span className="pointer-events-none absolute bottom-1 right-1 rounded bg-black/60 px-1 text-[10px] text-white">
          {Math.round(asset.duration_sec)}s
        </span>
      ) : null}
      {selectionMode && (
        <span
          className={`absolute left-1 top-1 flex h-5 w-5 items-center justify-center rounded-full border-2 border-white text-[10px] ${
            selected ? "bg-brand-500 text-white" : "bg-black/40 text-transparent"
          }`}
        >
          ✓
        </span>
      )}
      {!selectionMode && asset.favorite && (
        <span
          className="absolute right-1 top-1 text-yellow-400"
          title="已收藏"
          aria-label="已收藏"
        >
          ★
        </span>
      )}
      {!selectionMode && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onToggleFavorite(asset);
          }}
          title={asset.favorite ? "取消收藏" : "收藏"}
          className="absolute right-1 top-1 hidden h-7 w-7 items-center justify-center rounded-full bg-black/40 text-base text-white hover:bg-black/60 group-hover:flex"
        >
          {asset.favorite ? "★" : "☆"}
        </button>
      )}
    </div>
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

type FavFilter = "all" | "favorites" | "unfavorites";

export default function Timeline() {
  const [mediaType, setMediaType] = useState<string | undefined>();
  const [favFilter, setFavFilter] = useState<FavFilter>("all");
  const [preview, setPreview] = useState<AssetDTO | null>(null);
  const [selectionMode, setSelectionMode] = useState(false);
  const sel = useSelection();
  const qc = useQueryClient();

  const favoriteParam: boolean | undefined =
    favFilter === "favorites" ? true : favFilter === "unfavorites" ? false : undefined;

  const {
    data,
    fetchNextPage,
    hasNextPage,
    isFetching,
    isFetchingNextPage,
  } = useInfiniteQuery({
    queryKey: ["assets", mediaType, favoriteParam],
    queryFn: ({ pageParam }) => api.assets(pageParam, mediaType, undefined, favoriteParam),
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

  const toggleFavorite = useMutation({
    mutationFn: (a: AssetDTO) => api.toggleFavorite(a.id),
    onMutate: async (a) => {
      await qc.cancelQueries({ queryKey: ["assets"] });
      const prev = qc.getQueryData(["assets", mediaType, favoriteParam]);
      qc.setQueryData(["assets", mediaType, favoriteParam], (old: any) => {
        if (!old) return old;
        return {
          ...old,
          pages: old.pages.map((p: any) => ({
            ...p,
            items: p.items.map((it: AssetDTO) =>
              it.id === a.id ? { ...it, favorite: !it.favorite } : it,
            ),
          })),
        };
      });
      return { prev };
    },
    onError: (_e, _a, ctx) => {
      if (ctx?.prev) qc.setQueryData(["assets", mediaType, favoriteParam], ctx.prev);
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["asset"] });
    },
  });

  const handleClick = (asset: AssetDTO) => {
    if (selectionMode) sel.toggle(asset.id);
    else setPreview(asset);
  };

  const gridEmpty = (
    <p className="mt-10 text-center text-neutral-400">
      还没有媒体文件。点击左上「立即扫描媒体目录」，或到「上传」页上传。
    </p>
  );

  return (
    <div className="flex h-full flex-col">
      <div className="shrink-0 p-4 pb-2 md:p-6 md:pb-2">
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <h1 className="text-xl font-semibold">时间轴</h1>
          <button
            onClick={() => {
              setSelectionMode((v) => !v);
              sel.clear();
            }}
            className={`rounded-md px-3 py-1 text-xs transition ${
              selectionMode
                ? "bg-brand-500 text-white"
                : "bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300"
            }`}
          >
            {selectionMode ? "退出选择" : "选择"}
          </button>
          <div className="ml-auto flex gap-1">
            {(["all", "favorites", "unfavorites"] as FavFilter[]).map((f) => (
              <button
                key={f}
                onClick={() => setFavFilter(f)}
                className={`rounded-md px-3 py-1 text-xs transition ${
                  favFilter === f
                    ? "bg-brand-500 text-white"
                    : "bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300"
                }`}
              >
                {f === "all" ? "全部" : f === "favorites" ? "★ 收藏" : "未收藏"}
              </button>
            ))}
          </div>
          <div className="flex gap-1">
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
      </div>

      <div className="min-h-0 flex-1 px-4 md:px-6">
        <JustifiedGrid
          assets={items}
          renderItem={(item) => (
            <GridCell
              key={item.asset.id}
              item={item}
              asset={item.asset}
              selected={sel.isSelected(item.asset.id)}
              selectionMode={selectionMode}
              onClick={handleClick}
              onToggleFavorite={(a) => toggleFavorite.mutate(a)}
            />
          )}
          empty={gridEmpty}
          className="h-full"
        />
      </div>

      {isFetchingNextPage && (
        <p className="shrink-0 py-2 text-center text-sm text-neutral-400">加载更多…</p>
      )}

      {preview && <Lightbox asset={preview} onClose={() => setPreview(null)} />}

      <BulkBar selectedIds={sel.ids} context="library" onClear={sel.clear} />
    </div>
  );
}
