import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type ShareLinkDTO } from "../lib/api";

function fmtExpires(ts: number | null): string {
  if (!ts) return "永不过期";
  const d = new Date(ts * 1000);
  return d.toLocaleString("zh-CN");
}

function copyText(text: string) {
  navigator.clipboard.writeText(text).catch(() => {});
}

export function ShareModal({ albumId, onClose }: { albumId: number; onClose: () => void }) {
  const qc = useQueryClient();
  const [allowOriginal, setAllowOriginal] = useState(false);
  const [allowDownload, setAllowDownload] = useState(false);
  const [expiresDays, setExpiresDays] = useState("");
  const [copied, setCopied] = useState<string | null>(null);

  const shares = useQuery({
    queryKey: ["shares", albumId],
    queryFn: () => api.listShares(albumId),
  });

  const create = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = {
        allow_original: allowOriginal,
        allow_download: allowDownload,
      };
      if (expiresDays) {
        const days = parseInt(expiresDays, 10);
        if (days > 0) {
          body.expires_at = Math.floor(Date.now() / 1000) + days * 86400;
        }
      }
      return api.createShare(albumId, body);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["shares", albumId] }),
  });

  const del = useMutation({
    mutationFn: (token: string) => api.deleteShare(albumId, token),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["shares", albumId] }),
  });

  const origin = typeof window !== "undefined" ? window.location.origin : "";
  const items = shares.data?.items ?? [];

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-md bg-white p-5 dark:bg-neutral-900"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-base font-semibold">分享相册</h2>
        <p className="mt-1 text-xs text-neutral-500">
          生成公开链接后，任何人都能通过该链接查看此相册中的照片。
        </p>

        <div className="mt-4 space-y-3">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={allowOriginal}
              onChange={(e) => {
                setAllowOriginal(e.target.checked);
                if (!e.target.checked) setAllowDownload(false);
              }}
              className="rounded border-neutral-300"
            />
            允许查看原图
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={allowDownload}
              disabled={!allowOriginal}
              onChange={(e) => setAllowDownload(e.target.checked)}
              className="rounded border-neutral-300 disabled:opacity-50"
            />
            允许下载原图
          </label>
          <div className="flex items-center gap-2 text-sm">
            <span className="text-neutral-500">有效期</span>
            <select
              value={expiresDays}
              onChange={(e) => setExpiresDays(e.target.value)}
              className="rounded border border-neutral-300 px-2 py-1 text-sm dark:border-neutral-600 dark:bg-neutral-800"
            >
              <option value="">永不过期</option>
              <option value="1">1 天</option>
              <option value="7">7 天</option>
              <option value="30">30 天</option>
            </select>
          </div>
        </div>

        <div className="mt-4 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-md px-3 py-1.5 text-sm text-neutral-500 hover:bg-neutral-100 dark:hover:bg-neutral-800"
          >
            关闭
          </button>
          <button
            onClick={() => create.mutate()}
            disabled={create.isPending}
            className="rounded-md bg-brand-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-600 disabled:opacity-50"
          >
            {create.isPending ? "生成中…" : "生成分享链接"}
          </button>
        </div>

        {create.isError && (
          <p className="mt-2 text-sm text-red-500">
            生成失败：{(create.error as Error).message}
          </p>
        )}

        {items.length > 0 && (
          <div className="mt-5 border-t border-neutral-200 pt-4 dark:border-neutral-700">
            <h3 className="text-sm font-medium">已生效的链接</h3>
            <ul className="mt-2 space-y-2">
              {items.map((s: ShareLinkDTO) => {
                const fullUrl = `${origin}${s.share_url}`;
                return (
                  <li
                    key={s.token}
                    className="rounded border border-neutral-200 p-2 text-xs dark:border-neutral-700"
                  >
                    <div className="flex items-center gap-2">
                      <span className="truncate font-mono text-neutral-600 dark:text-neutral-300">
                        {s.share_url}
                      </span>
                      <button
                        onClick={() => {
                          copyText(fullUrl);
                          setCopied(s.token);
                          setTimeout(() => setCopied(null), 1500);
                        }}
                        className="shrink-0 rounded bg-neutral-100 px-2 py-0.5 hover:bg-neutral-200 dark:bg-neutral-800 dark:hover:bg-neutral-700"
                      >
                        {copied === s.token ? "已复制" : "复制"}
                      </button>
                      <button
                        onClick={() => del.mutate(s.token)}
                        disabled={del.isPending}
                        className="shrink-0 rounded bg-red-50 px-2 py-0.5 text-red-600 hover:bg-red-100 dark:bg-red-950 dark:hover:bg-red-900"
                      >
                        撤销
                      </button>
                    </div>
                    <div className="mt-1 text-neutral-400">
                      {s.allow_original ? "可查看原图" : "仅缩略图"}
                      {s.allow_download ? " · 可下载" : ""}
                      {" · "}
                      {fmtExpires(s.expires_at)}
                    </div>
                  </li>
                );
              })}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
