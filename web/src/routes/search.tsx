import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, mediaLabel, thumbUrl, type SearchHitDTO } from "../lib/api";

function formatTime(sec: number | null): string {
  if (sec == null) return "–";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function HitCard({ hit }: { hit: SearchHitDTO }) {
  const [open, setOpen] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const isImage = hit.media_type === "image";

  useEffect(() => {
    if (open && !isImage && videoRef.current && hit.can_seek && hit.t_start != null) {
      videoRef.current.currentTime = hit.t_start;
    }
  }, [open, isImage, hit.can_seek, hit.t_start]);

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="group relative m-[2px] aspect-[4/3] overflow-hidden rounded-sm bg-neutral-200 text-left dark:bg-neutral-800"
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
        <span className="absolute left-1 top-1 rounded bg-black/60 px-1 text-[10px] text-white">
          #{hit.rank}
        </span>
        {hit.can_seek && hit.t_start != null && (
          <span className="absolute bottom-1 left-1 rounded bg-black/60 px-1 text-[10px] text-white">
            ▶ {formatTime(hit.t_start)}–{formatTime(hit.t_end)}
          </span>
        )}
      </button>

      {open && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 p-4"
          onClick={() => setOpen(false)}
        >
          <div className="max-h-full max-w-full overflow-auto" onClick={(e) => e.stopPropagation()}>
            {isImage ? (
              <img
                src={`/api/assets/${hit.asset_id}/file`}
                alt=""
                className="max-h-[90vh] max-w-full object-contain"
              />
            ) : (
              <video
                ref={videoRef}
                src={`/api/assets/${hit.asset_id}/file`}
                controls
                autoPlay
                className="max-h-[90vh] max-w-full"
              />
            )}
          </div>
          <button
            onClick={() => setOpen(false)}
            className="absolute right-4 top-4 rounded-full bg-white/10 px-3 py-1.5 text-sm text-white hover:bg-white/20"
          >
            ✕
          </button>
          <Link
            to={
              hit.can_seek && hit.t_start != null
                ? `/asset/${hit.asset_id}?t=${hit.t_start}`
                : `/asset/${hit.asset_id}`
            }
            className="absolute bottom-4 right-4 rounded-full bg-white/10 px-3 py-1.5 text-sm text-white hover:bg-white/20"
            onClick={(e) => e.stopPropagation()}
          >
            详情
          </Link>
        </div>
      )}
    </>
  );
}

export default function Search() {
  const [q, setQ] = useState("");
  const [submitted, setSubmitted] = useState("");

  const { data, isFetching } = useQuery({
    queryKey: ["search", submitted],
    queryFn: () => api.search(submitted, 40),
    enabled: submitted.trim().length > 0,
  });

  const items: SearchHitDTO[] = data?.items ?? [];

  return (
    <div className="p-4 md:p-6">
      <h1 className="mb-4 text-xl font-semibold">搜索</h1>

      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          setSubmitted(q.trim());
        }}
      >
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="试试：海滩 / 日落 / scope:scene 夜景 …"
          className="flex-1 rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm outline-none focus:border-brand-500 dark:border-neutral-700 dark:bg-neutral-900"
        />
        <button
          type="submit"
          disabled={isFetching || !q.trim()}
          className="rounded-md bg-brand-500 px-4 py-2 text-sm font-medium text-white hover:bg-brand-600 disabled:opacity-50"
        >
          {isFetching ? "搜索中…" : "搜索"}
        </button>
      </form>

      {submitted && data && (
        <p className="mb-2 mt-3 text-sm text-neutral-500">
          共 {data.total} 条结果，匹配「{data.query}」
        </p>
      )}

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6">
        {items.map((h) => (
          <HitCard key={`${h.asset_id}-${h.scope}-${h.t_start ?? ""}`} hit={h} />
        ))}
      </div>

      {submitted && !isFetching && items.length === 0 && (
        <p className="mt-10 text-center text-neutral-400">
          没有匹配的结果。可尝试更短的关键词，或等待索引完成后重试。
        </p>
      )}

      {!submitted && (
        <p className="mt-10 text-center text-neutral-400">
          输入关键词开始搜索。视频结果支持从命中的场景秒直接播放。
        </p>
      )}
    </div>
  );
}
