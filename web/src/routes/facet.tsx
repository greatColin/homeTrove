import { useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, mediaLabel, thumbUrl, type AssetDTO } from "../lib/api";
import { FacetChips } from "../components/kv";

function FacetThumb({ asset }: { asset: AssetDTO }) {
  const isImage = asset.media_type === "image";
  return (
    <Link
      to={`/asset/${asset.id}`}
      className="group relative m-[2px] aspect-[4/3] overflow-hidden rounded-sm bg-neutral-200 dark:bg-neutral-800"
    >
      <img
        src={thumbUrl(asset.id, isImage ? "small" : "placeholder")}
        alt=""
        loading="lazy"
        className="h-full w-full object-cover transition group-hover:scale-105"
        onError={(e) => {
          (e.currentTarget as HTMLImageElement).style.display = "none";
        }}
      />
      {!isImage && (
        <span className="absolute inset-0 flex items-center justify-center text-xs font-medium text-white/90">
          <span className="rounded bg-black/50 px-2 py-0.5">{mediaLabel(asset.media_type)}</span>
        </span>
      )}
    </Link>
  );
}

export default function FacetPage({
  facet,
  title,
  emptyHint,
}: {
  facet: "tags" | "categories";
  title: string;
  emptyHint: string;
}) {
  const [selected, setSelected] = useState<string | undefined>();
  const { data: facets } = useQuery({ queryKey: ["facets"], queryFn: api.facets });
  const counts = facets?.[facet] ?? {};

  const filterKey = facet === "tags" ? "tag" : "category";

  const {
    data,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useInfiniteQuery({
    queryKey: ["facet-assets", filterKey, selected],
    queryFn: ({ pageParam }) =>
      api.assets(pageParam, undefined, selected ? { [filterKey]: selected } : {}),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled: selected != null,
  });

  const items = data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <div className="p-4 md:p-6">
      <h1 className="text-xl font-semibold">{title}</h1>
      <p className="mt-1 text-sm text-neutral-500">{emptyHint}</p>

      <div className="mt-4">
        <FacetChips
          facets={counts}
          selected={selected}
          onSelect={(v) => setSelected(v === selected ? undefined : v)}
        />
      </div>

      {selected && (
        <>
          <p className="mt-4 text-sm text-neutral-500">
            选中「{selected}」：{items.length} 个文件
            {hasNextPage && (
              <button
                onClick={() => fetchNextPage()}
                disabled={isFetchingNextPage}
                className="ml-2 text-brand-500 hover:underline disabled:opacity-50"
              >
                {isFetchingNextPage ? "加载中…" : "加载更多"}
              </button>
            )}
          </p>
          <div className="mt-3 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6">
            {items.map((a) => (
              <FacetThumb key={a.id} asset={a} />
            ))}
          </div>
          {items.length === 0 && !isFetchingNextPage && (
            <p className="mt-6 text-center text-neutral-400">该分类下暂无文件</p>
          )}
        </>
      )}

      {!selected && (
        <p className="mt-8 text-center text-sm text-neutral-400">
          点击上方标签查看对应文件
        </p>
      )}
    </div>
  );
}
