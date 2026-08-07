import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";

/**
 * Multi-select state + bulk action bar.
 *
 * The selection lives in component state (so navigating away clears it).
 * For routes that survive the bar — like the timeline — the bar renders
 * fixed at the bottom and exposes the four bulk actions v1 ships with:
 *
 *   * move to trash      → /bulk/assets/trash
 *   * restore            → /bulk/assets/restore
 *   * favorite           → /bulk/assets/favorite
 *   * unfavorite         → /bulk/assets/unfavorite
 *   * add to album       → /bulk/assets/add-to-album
 *
 * Each action reports the partial-failure count back to the user (the
 * ``missing`` / ``affected`` numbers) so silent failures don't surprise
 * the operator. After each successful action we invalidate the relevant
 * React Query keys.
 */

export interface BulkBarProps {
  selectedIds: number[];
  context: "library" | "trash" | "folder" | "album";
  onClear: () => void;
}

export function useSelection() {
  // ``Set`` keeps O(1) add/remove/has; expose a stable array for the UI.
  const [selected, setSelected] = useState<Set<number>>(() => new Set());

  const isSelected = (id: number) => selected.has(id);

  const toggle = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const replace = (ids: number[]) => setSelected(new Set(ids));

  const clear = () => setSelected(new Set());

  const ids = useMemo(() => Array.from(selected), [selected]);

  return { selected, ids, isSelected, toggle, replace, clear };
}

/** Bulk-action toolbar. Renders nothing when ``selectedIds`` is empty. */
export function BulkBar({ selectedIds, context, onClear }: BulkBarProps) {
  const qc = useQueryClient();
  const [albumPickerOpen, setAlbumPickerOpen] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  const trash = useMutation({
    mutationFn: () => api.bulkTrash(selectedIds),
    onSuccess: (res) => {
      invalidateAll(qc);
      setFeedback(
        `已移到回收站：${res.affected}${res.missing.length ? `（缺失 ${res.missing.length}）` : ""}`,
      );
      onClear();
    },
    onError: (e) => setFeedback(`失败：${(e as Error).message}`),
  });

  const restore = useMutation({
    mutationFn: () => api.bulkRestore(selectedIds),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["trash"] });
      qc.invalidateQueries({ queryKey: ["assets"] });
      qc.invalidateQueries({ queryKey: ["asset"] });
      setFeedback(`已还原：${res.affected}${res.missing.length ? `（缺失 ${res.missing.length}）` : ""}`);
      onClear();
    },
    onError: (e) => setFeedback(`失败：${(e as Error).message}`),
  });

  const fav = useMutation({
    mutationFn: (on: boolean) => api.bulkFavorite(selectedIds, on),
    onSuccess: (res, on) => {
      qc.invalidateQueries({ queryKey: ["assets"] });
      qc.invalidateQueries({ queryKey: ["asset"] });
      setFeedback(
        `${on ? "已收藏" : "已取消收藏"}：${res.affected}${res.missing.length ? `（缺失 ${res.missing.length}）` : ""}`,
      );
      onClear();
    },
    onError: (e) => setFeedback(`失败：${(e as Error).message}`),
  });

  const addToAlbum = useMutation({
    mutationFn: (albumId: number) => api.bulkAddToAlbum(selectedIds, albumId),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["albums"] });
      setFeedback(
        `已添加到相册：${res.added}${res.missing.length ? `（缺失 ${res.missing.length}）` : ""}`,
      );
      onClear();
      setAlbumPickerOpen(false);
    },
    onError: (e) => setFeedback(`失败：${(e as Error).message}`),
  });

  // Auto-dismiss the toast after a short window so it doesn't accumulate.
  useEffect(() => {
    if (!feedback) return;
    const t = setTimeout(() => setFeedback(null), 3500);
    return () => clearTimeout(t);
  }, [feedback]);

  if (selectedIds.length === 0) return null;

  return (
    <>
      <div className="fixed inset-x-0 bottom-0 z-40 border-t border-neutral-200 bg-white shadow-lg dark:border-neutral-800 dark:bg-neutral-950">
        <div className="mx-auto flex max-w-screen-2xl items-center gap-2 px-4 py-3">
          <span className="text-sm font-medium">
            已选 {selectedIds.length} 项
          </span>
          <button
            onClick={onClear}
            className="rounded-md px-2 py-1 text-xs text-neutral-500 hover:bg-neutral-100 dark:hover:bg-neutral-800"
          >
            清除选择
          </button>
          <div className="ml-auto flex flex-wrap gap-2">
            {context === "library" && (
              <>
                <button
                  onClick={() => fav.mutate(true)}
                  disabled={fav.isPending}
                  className="rounded-md bg-yellow-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-yellow-600 disabled:opacity-50"
                >
                  收藏
                </button>
                <button
                  onClick={() => fav.mutate(false)}
                  disabled={fav.isPending}
                  className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm hover:bg-neutral-100 disabled:opacity-50 dark:border-neutral-700 dark:hover:bg-neutral-800"
                >
                  取消收藏
                </button>
                <button
                  onClick={() => setAlbumPickerOpen(true)}
                  className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800"
                >
                  添加到相册…
                </button>
                <button
                  onClick={() => {
                    if (confirm(`确定将这 ${selectedIds.length} 项移到回收站？`)) trash.mutate();
                  }}
                  disabled={trash.isPending}
                  className="rounded-md border border-red-300 px-3 py-1.5 text-sm text-red-600 hover:bg-red-50 disabled:opacity-50 dark:border-red-700 dark:text-red-400 dark:hover:bg-red-500/10"
                >
                  移到回收站
                </button>
              </>
            )}
            {context === "trash" && (
              <>
                <button
                  onClick={() => restore.mutate()}
                  disabled={restore.isPending}
                  className="rounded-md bg-brand-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-600 disabled:opacity-50"
                >
                  还原
                </button>
              </>
            )}
          </div>
        </div>
      </div>

      {albumPickerOpen && (
        <AlbumPicker
          onPick={(id) => addToAlbum.mutate(id)}
          onClose={() => setAlbumPickerOpen(false)}
          loading={addToAlbum.isPending}
        />
      )}

      {feedback && (
        <div className="fixed bottom-20 left-1/2 z-50 -translate-x-1/2 rounded-md bg-neutral-900/90 px-4 py-2 text-sm text-white shadow-lg">
          {feedback}
        </div>
      )}
    </>
  );
}

function AlbumPicker({
  onPick,
  onClose,
  loading,
}: {
  onPick: (albumId: number) => void;
  onClose: () => void;
  loading: boolean;
}) {
  const albums = useQueryAlbums();
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-md bg-white p-5 dark:bg-neutral-900"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-base font-semibold">添加到相册</h2>
        {albums.isLoading && <p className="mt-3 text-sm text-neutral-400">加载中…</p>}
        {albums.data && albums.data.items.length === 0 && (
          <p className="mt-3 text-sm text-neutral-400">
            还没有相册。请先到「相册」页面创建一个。
          </p>
        )}
        {albums.data && albums.data.items.length > 0 && (
          <ul className="mt-3 max-h-80 divide-y divide-neutral-100 overflow-auto dark:divide-neutral-800">
            {albums.data.items.map((a) => (
              <li key={a.id}>
                <button
                  onClick={() => onPick(a.id)}
                  disabled={loading}
                  className="flex w-full items-center justify-between px-2 py-2 text-left text-sm hover:bg-neutral-100 disabled:opacity-50 dark:hover:bg-neutral-800"
                >
                  <span className="truncate">{a.name}</span>
                  <span className="text-xs text-neutral-400">{a.asset_count} 项</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="mt-4 flex justify-end">
          <button
            onClick={onClose}
            className="rounded-md px-3 py-1.5 text-sm text-neutral-500 hover:bg-neutral-100 dark:hover:bg-neutral-800"
          >
            取消
          </button>
        </div>
      </div>
    </div>
  );
}

function useQueryAlbums() {
  return useQuery({
    queryKey: ["albums"],
    queryFn: () => api.albums(),
  });
}

/** Reusable invalidation: any successful bulk mutation touches these. */
function invalidateAll(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: ["assets"] });
  qc.invalidateQueries({ queryKey: ["trash"] });
  qc.invalidateQueries({ queryKey: ["asset"] });
  qc.invalidateQueries({ queryKey: ["albums"] });
  qc.invalidateQueries({ queryKey: ["search"] });
  qc.invalidateQueries({ queryKey: ["folders"] });
  qc.invalidateQueries({ queryKey: ["places"] });
}
