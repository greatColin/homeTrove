import { useEffect, useState } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, mediaLabel, thumbUrl, type AssetDTO, type AssetsFilters } from "../lib/api";
import { BulkBar, useSelection } from "../components/bulk_actions";
import { JustifiedGrid, type LayoutItem } from "../components/justified_grid";
import { MutedVideoPreview } from "../components/muted_preview";

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
  const [hovered, setHovered] = useState(false);
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
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
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
        className="relative block h-full w-full"
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
        {!isImage && hovered && <MutedVideoPreview assetId={asset.id} />}
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

interface FilterState {
  mediaType: string | undefined;
  favFilter: FavFilter;
  takenAfter: string | undefined;
  takenBefore: string | undefined;
  personId: number | undefined;
  place: string | undefined;
  tag: string | undefined;
}

const EMPTY_FILTERS: FilterState = {
  mediaType: undefined,
  favFilter: "all",
  takenAfter: undefined,
  takenBefore: undefined,
  personId: undefined,
  place: undefined,
  tag: undefined,
};

function dateToEpoch(date: string, endOfDay: boolean): number {
  return new Date(`${date}T${endOfDay ? "23:59:59" : "00:00:00"}`).getTime() / 1000;
}

function filterStateToApi(f: FilterState): AssetsFilters {
  const apiFilters: AssetsFilters = {
    mediaType: f.mediaType,
    tag: f.tag,
  };
  if (f.favFilter !== "all") apiFilters.favorite = f.favFilter === "favorites";
  if (f.takenAfter) apiFilters.takenAfter = dateToEpoch(f.takenAfter, false);
  if (f.takenBefore) apiFilters.takenBefore = dateToEpoch(f.takenBefore, true);
  if (f.personId !== undefined) apiFilters.personId = f.personId;
  if (f.place) apiFilters.place = f.place;
  return apiFilters;
}

function activeFilterCount(f: FilterState): number {
  let n = 0;
  if (f.mediaType) n++;
  if (f.favFilter !== "all") n++;
  if (f.takenAfter) n++;
  if (f.takenBefore) n++;
  if (f.personId !== undefined) n++;
  if (f.place) n++;
  if (f.tag) n++;
  return n;
}

function FilterPanel({
  filters,
  onChange,
}: {
  filters: FilterState;
  onChange: (f: FilterState) => void;
}) {
  const { data: persons } = useQuery({ queryKey: ["persons"], queryFn: api.persons });
  const { data: places } = useQuery({ queryKey: ["places"], queryFn: api.places });
  const { data: facets } = useQuery({ queryKey: ["facets"], queryFn: api.facets });

  const tags = facets?.tags ?? {};
  const tagNames = Object.keys(tags).sort((a, b) => tags[b] - tags[a]);

  const set = (patch: Partial<FilterState>) => onChange({ ...filters, ...patch });

  const selectCls =
    "rounded-md border border-neutral-300 bg-white px-2 py-1.5 text-sm dark:border-neutral-600 dark:bg-neutral-900";
  const labelCls = "mb-1 block text-xs font-medium text-neutral-500";

  return (
    <div className="mt-3 rounded-lg border border-neutral-200 p-3 dark:border-neutral-700">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <label className={labelCls}>媒体类型</label>
          <select
            className={selectCls}
            value={filters.mediaType ?? ""}
            onChange={(e) => set({ mediaType: e.target.value || undefined })}
          >
            <option value="">全部</option>
            <option value="image">图片</option>
            <option value="video">视频</option>
            <option value="other">其他</option>
          </select>
        </div>

        <div>
          <label className={labelCls}>收藏状态</label>
          <select
            className={selectCls}
            value={filters.favFilter}
            onChange={(e) => set({ favFilter: e.target.value as FavFilter })}
          >
            <option value="all">全部</option>
            <option value="favorites">已收藏</option>
            <option value="unfavorites">未收藏</option>
          </select>
        </div>

        <div>
          <label className={labelCls}>拍摄日期：开始</label>
          <input
            type="date"
            className={selectCls}
            value={filters.takenAfter ?? ""}
            onChange={(e) => set({ takenAfter: e.target.value || undefined })}
          />
        </div>

        <div>
          <label className={labelCls}>拍摄日期：结束</label>
          <input
            type="date"
            className={selectCls}
            value={filters.takenBefore ?? ""}
            onChange={(e) => set({ takenBefore: e.target.value || undefined })}
          />
        </div>

        <div>
          <label className={labelCls}>人物</label>
          <select
            className={selectCls}
            value={filters.personId ?? ""}
            onChange={(e) =>
              set({ personId: e.target.value ? Number(e.target.value) : undefined })
            }
          >
            <option value="">全部</option>
            {(persons?.items ?? []).map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}（{p.face_count}）
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className={labelCls}>地点</label>
          <select
            className={selectCls}
            value={filters.place ?? ""}
            onChange={(e) => set({ place: e.target.value || undefined })}
          >
            <option value="">全部</option>
            {(places?.items ?? []).map((pl) => {
              const key = `${pl.grid[0]},${pl.grid[1]}`;
              return (
                <option key={key} value={key}>
                  {pl.lat.toFixed(2)}, {pl.lon.toFixed(2)} · {pl.count}
                </option>
              );
            })}
          </select>
        </div>

        <div>
          <label className={labelCls}>标签</label>
          {tagNames.length === 0 ? (
            <p className="text-sm text-neutral-400">暂无标签</p>
          ) : (
            <select
              className={selectCls}
              value={filters.tag ?? ""}
              onChange={(e) => set({ tag: e.target.value || undefined })}
            >
              <option value="">全部</option>
              {tagNames.map((t) => (
                <option key={t} value={t}>
                  {t}（{tags[t]}）
                </option>
              ))}
            </select>
          )}
        </div>
      </div>

      <div className="mt-3 flex items-center justify-between">
        <span className="text-sm text-neutral-500">
          已启用 {activeFilterCount(filters)} 个筛选条件（条件间为 AND 关系）
        </span>
        <button
          onClick={() => onChange(EMPTY_FILTERS)}
          className="rounded-md bg-neutral-100 px-3 py-1 text-xs text-neutral-600 hover:bg-neutral-200 dark:bg-neutral-800 dark:text-neutral-300 dark:hover:bg-neutral-700"
        >
          清除全部
        </button>
      </div>
    </div>
  );
}

export default function Timeline() {
  const [filters, setFilters] = useState<FilterState>(EMPTY_FILTERS);
  const [panelOpen, setPanelOpen] = useState(false);
  const [preview, setPreview] = useState<AssetDTO | null>(null);
  const [selectionMode, setSelectionMode] = useState(false);
  const sel = useSelection();
  const qc = useQueryClient();

  const apiFilters = filterStateToApi(filters);
  const activeCount = activeFilterCount(filters);

  const {
    data,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useInfiniteQuery({
    queryKey: ["assets", apiFilters],
    queryFn: ({ pageParam }) => api.assets(pageParam, apiFilters),
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
      const prev = qc.getQueryData(["assets", apiFilters]);
      qc.setQueryData(["assets", apiFilters], (old: any) => {
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
      if (ctx?.prev) qc.setQueryData(["assets", apiFilters], ctx.prev);
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["asset"] });
    },
  });

  const handleClick = (asset: AssetDTO) => {
    if (selectionMode) sel.toggle(asset.id);
    else setPreview(asset);
  };

  const gridEmpty = activeCount > 0 ? (
    <p className="mt-10 text-center text-neutral-400">没有符合当前筛选条件的资产。</p>
  ) : (
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
          <button
            onClick={() => setPanelOpen((v) => !v)}
            className={`relative rounded-md px-3 py-1 text-xs transition ${
              panelOpen || activeCount > 0
                ? "bg-brand-500 text-white"
                : "bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300"
            }`}
          >
            筛选{activeCount > 0 ? `（${activeCount}）` : ""}
          </button>
          {activeCount > 0 && (
            <button
              onClick={() => setFilters(EMPTY_FILTERS)}
              className="rounded-md px-3 py-1 text-xs text-neutral-500 hover:text-neutral-700 dark:text-neutral-400 dark:hover:text-neutral-200"
            >
              清除全部筛选
            </button>
          )}
        </div>

        {panelOpen && <FilterPanel filters={filters} onChange={setFilters} />}
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
