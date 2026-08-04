import { useParams, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, mediaLabel } from "../lib/api";
import { PluginDataBlock, PLUGIN_LABELS } from "../components/kv";

function fmtTs(ts: number | null | undefined): string {
  if (!ts) return "–";
  return new Date(ts * 1000).toLocaleString("zh-CN");
}

export default function AssetDetail() {
  const { id } = useParams();
  const assetId = Number(id);
  const { data: asset, isLoading, isError } = useQuery({
    queryKey: ["asset", assetId],
    queryFn: () => api.asset(assetId),
    enabled: Number.isFinite(assetId),
  });

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

  return (
    <div className="p-4 md:p-6">
      <Link to="/timeline" className="text-sm text-brand-500 hover:underline">
        ← 返回时间轴
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
            <video
              src={`/api/assets/${asset.id}/file`}
              controls
              className="w-full"
            />
          ) : (
            <div className="flex h-48 items-center justify-center text-neutral-400">
              {mediaLabel(asset.media_type)}
            </div>
          )}
        </div>

        <div className="min-w-0 flex-1">
          <h1 className="break-all text-lg font-semibold">{fileName}</h1>
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

          <div className="mt-6 grid gap-4">
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
