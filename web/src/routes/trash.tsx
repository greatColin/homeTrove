import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, mediaLabel, thumbUrl } from "../lib/api";
import { BulkBar, useSelection } from "../components/bulk_actions";

function fmtAge(epoch: number | null): string {
  if (!epoch) return "–";
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - epoch));
  if (seconds < 60) return `${seconds} 秒前`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  return `${days} 天前`;
}

export default function Trash() {
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState<null | "all" | "older">(null);
  const [selectionMode, setSelectionMode] = useState(false);
  const sel = useSelection();

  const trash = useQuery({
    queryKey: ["trash"],
    queryFn: () => api.trash(200, 0),
  });

  const restore = useMutation({
    mutationFn: (id: number) => api.restoreFromTrash(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["trash"] }),
  });

  const retash = useMutation({
    mutationFn: (id: number) => api.moveToTrash(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["trash"] }),
  });

  const empty = useMutation({
    mutationFn: (olderThanSeconds?: number) => api.emptyTrash(olderThanSeconds),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["trash"] });
      setConfirming(null);
      alert(`已永久删除 ${res.dropped} 项`);
    },
  });

  const items = trash.data?.items ?? [];
  const total = trash.data?.total ?? 0;
  const retentionDays = 30;

  return (
    <div className="p-4 pb-32 md:p-6 md:pb-32">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">回收站</h1>
        <span className="text-sm text-neutral-500">
          {total > 0 ? `共 ${total} 项` : "回收站为空"}
        </span>
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
        <div className="ml-auto flex gap-2">
          <button
            disabled={total === 0 || empty.isPending}
            onClick={() => setConfirming("older")}
            className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm transition hover:bg-neutral-100 disabled:opacity-50 dark:border-neutral-700 dark:hover:bg-neutral-800"
          >
            清空 {retentionDays} 天前的
          </button>
          <button
            disabled={total === 0 || empty.isPending}
            onClick={() => setConfirming("all")}
            className="rounded-md bg-red-500 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-red-600 disabled:opacity-50"
          >
            永久清空回收站
          </button>
        </div>
      </div>

      <p className="mb-4 text-xs text-neutral-500">
        移到回收站的资源会在保留 {retentionDays} 天后自动永久删除。文件本身不会被删除——它仍位于媒体根目录的原始位置，HomeTrove
        只是不再索引它。要恢复原始索引，点击「还原」。
      </p>

      {trash.isLoading && <p className="text-sm text-neutral-400">加载中…</p>}
      {!trash.isLoading && items.length === 0 && (
        <p className="mt-10 text-center text-neutral-400">回收站是空的。</p>
      )}

      {items.length > 0 && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6">
          {items.map((a) => {
            const isImage = a.media_type === "image";
            const isSelected = sel.isSelected(a.id);
            return (
              <div
                key={a.id}
                onClick={() => {
                  if (selectionMode) sel.toggle(a.id);
                }}
                className={`relative cursor-pointer overflow-hidden rounded-md border bg-white dark:bg-neutral-900 ${
                  isSelected
                    ? "border-brand-500 outline outline-2 outline-brand-500"
                    : "border-neutral-200 dark:border-neutral-800"
                }`}
              >
                {selectionMode && (
                  <span
                    className={`absolute left-2 top-2 z-10 flex h-5 w-5 items-center justify-center rounded-full border-2 border-white text-[10px] ${
                      isSelected ? "bg-brand-500 text-white" : "bg-black/40 text-transparent"
                    }`}
                  >
                    ✓
                  </span>
                )}
                <Link
                  to={`/asset/${a.id}`}
                  onClick={(e) => {
                    if (selectionMode) e.preventDefault();
                  }}
                  className="relative block aspect-[4/3] bg-neutral-100 dark:bg-neutral-800"
                >
                  <img
                    src={thumbUrl(a.id, isImage ? "small" : "placeholder")}
                    alt=""
                    loading="lazy"
                    className="h-full w-full object-cover"
                    onError={(e) => {
                      e.currentTarget.style.display = "none";
                    }}
                  />
                  {!isImage && (
                    <span className="absolute inset-0 flex items-center justify-center text-xs font-medium text-white/90">
                      <span className="rounded bg-black/50 px-2 py-0.5">
                        {mediaLabel(a.media_type)}
                      </span>
                    </span>
                  )}
                </Link>
                <div className="px-2 py-1.5">
                  <p className="truncate text-xs text-neutral-700 dark:text-neutral-300">
                    {a.path.split("\0").pop() ?? a.path}
                  </p>
                  <p className="mt-0.5 text-[10px] text-neutral-400">
                    移入 {fmtAge(a.deleted_at)}
                  </p>
                  <div className="mt-2 flex gap-1">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        restore.mutate(a.id);
                      }}
                      disabled={restore.isPending}
                      className="flex-1 rounded bg-brand-500 px-2 py-1 text-[11px] font-medium text-white hover:bg-brand-600 disabled:opacity-50"
                    >
                      还原
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        retash.mutate(a.id);
                      }}
                      disabled={retash.isPending}
                      title="将移入时间刷新为现在（不影响文件）"
                      className="rounded border border-neutral-300 px-2 py-1 text-[11px] text-neutral-500 hover:bg-neutral-100 disabled:opacity-50 dark:border-neutral-700 dark:hover:bg-neutral-800"
                    >
                      刷新
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {confirming && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
          onClick={() => setConfirming(null)}
        >
          <div
            className="w-full max-w-sm rounded-md bg-white p-5 dark:bg-neutral-900"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="text-base font-semibold">
              {confirming === "all" ? "永久清空回收站？" : `清空 ${retentionDays} 天前的项？`}
            </h2>
            <p className="mt-2 text-sm text-neutral-500">
              {confirming === "all"
                ? `将永久删除 ${total} 项数据库记录。这些文件仍位于媒体根目录的原始位置——HomeTrove 只是不再索引它们。`
                : `仅永久删除移入回收站超过 ${retentionDays} 天的项。`}
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => setConfirming(null)}
                className="rounded-md px-3 py-1.5 text-sm text-neutral-500 hover:bg-neutral-100 dark:hover:bg-neutral-800"
              >
                取消
              </button>
              <button
                onClick={() =>
                  empty.mutate(confirming === "older" ? retentionDays * 86400 : undefined)
                }
                disabled={empty.isPending}
                className="rounded-md bg-red-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-600 disabled:opacity-50"
              >
                确认永久删除
              </button>
            </div>
          </div>
        </div>
      )}

      <BulkBar selectedIds={sel.ids} context="trash" onClear={sel.clear} />
    </div>
  );
}
