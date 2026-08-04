import { useEffect } from "react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { api, mediaLabel, type FileJobItem, type JobStats } from "../lib/api";

function useJobSSE() {
  const qc = useQueryClient();
  useEffect(() => {
    const es = new EventSource("/api/jobs/stream");
    es.addEventListener("snapshot", (ev) => {
      const data = JSON.parse((ev as MessageEvent).data);
      qc.setQueryData(["job-sse"], data);
    });
    es.addEventListener("job-update", () => {
      void qc.invalidateQueries({ queryKey: ["jobs"] });
    });
    es.onerror = () => {
      /* EventSource auto-reconnects */
    };
    return () => es.close();
  }, [qc]);
}

function ProgressBar({ stats }: { stats: JobStats }) {
  const pct = Math.round(stats.progress * 100);
  return (
    <div>
      <div className="mb-1 flex justify-between text-xs text-neutral-500">
        <span>
          已完成 {stats.done} / {stats.total}
        </span>
        <span>{pct}%</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-neutral-200 dark:bg-neutral-700">
        <div
          className="h-full rounded-full bg-brand-500 transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

export default function Jobs() {
  useJobSSE();
  const { data } = useQuery({ queryKey: ["jobs"], queryFn: api.jobs, refetchInterval: 3000 });
  const qc = useQueryClient();
  const retry = useMutation({
    mutationFn: async (item: FileJobItem) => {
      const failed = item.jobs.filter((j) => j.state === "failed");
      for (const j of failed) {
        await api.retryJob(j.id);
      }
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["jobs"] }),
  });

  const stats = data?.stats;
  const items: FileJobItem[] = data?.items ?? [];

  function fmt(ts: number | null | undefined): string {
    if (!ts) return "–";
    return new Date(ts * 1000).toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  return (
    <div className="p-4 md:p-6">
      <h1 className="mb-4 text-xl font-semibold">索引任务</h1>

      {stats && <ProgressBar stats={stats} />}

      <div className="mt-4 grid grid-cols-2 gap-2 text-sm sm:grid-cols-5">
        {[
          ["待处理", stats?.pending],
          ["运行中", stats?.running],
          ["已完成", stats?.done],
          ["失败", stats?.failed],
        ].map(([label, n]) => (
          <div
            key={label as string}
            className="rounded-md border border-neutral-200 p-3 dark:border-neutral-800"
          >
            <div className="text-2xl font-semibold">{n ?? "–"}</div>
            <div className="text-xs text-neutral-500">{label}</div>
          </div>
        ))}
      </div>

      <div className="mt-6 overflow-x-auto rounded-md border border-neutral-200 dark:border-neutral-800">
        <table className="w-full min-w-[560px] text-left text-sm">
          <thead className="bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
            <tr>
              <th className="px-3 py-2">文件</th>
              <th className="px-3 py-2">类型</th>
              <th className="px-3 py-2">状态</th>
              <th className="px-3 py-2">插件</th>
              <th className="px-3 py-2">时间</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-neutral-100 dark:divide-neutral-800">
            {items.map((item) => {
              const failedJobs = item.jobs.filter((j) => j.state === "failed");
              return (
                <tr key={item.asset_id}>
                  <td className="max-w-[280px] truncate px-3 py-2 font-medium">
                    {item.filename}
                  </td>
                  <td className="px-3 py-2 text-neutral-500">
                    {item.media_type ? mediaLabel(item.media_type) : "–"}
                  </td>
                  <td className="px-3 py-2">
                    <span
                      className={`rounded px-1.5 py-0.5 text-xs ${
                        item.state === "done"
                          ? "bg-green-100 text-green-700 dark:bg-green-500/20 dark:text-green-300"
                          : item.state === "failed"
                            ? "bg-red-100 text-red-700 dark:bg-red-500/20 dark:text-red-300"
                            : "bg-amber-100 text-amber-700 dark:bg-amber-500/20 dark:text-amber-300"
                      }`}
                    >
                      {item.state === "active"
                        ? "索引中"
                        : item.state === "failed"
                          ? "失败"
                          : "完成"}
                    </span>
                  </td>
                  <td className="px-3 py-2 font-mono text-xs text-neutral-500">
                    {item.jobs.map((j) => j.plugin_id).join(", ")}
                  </td>
                  <td className="px-3 py-2 text-xs text-neutral-500">
                    {fmt(item.enqueued_at)}
                  </td>
                  <td className="px-3 py-2">
                    {failedJobs.length > 0 && (
                      <button
                        onClick={() => retry.mutate(item)}
                        disabled={retry.isPending}
                        className="rounded bg-brand-500 px-2 py-1 text-xs text-white hover:bg-brand-600 disabled:opacity-50"
                      >
                        重试
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {items.length === 0 && (
          <p className="p-6 text-center text-neutral-400">暂无任务</p>
        )}
      </div>

      {items.some((i) => i.state === "active") && (
        <p className="mt-2 text-xs text-neutral-500">
          文件正在后台索引，完成前会持续显示"索引中"。
        </p>
      )}
      <p className="mt-2 text-xs text-neutral-400">
        列表按文件倒序排列，最新添加的文件在最上方。
      </p>
    </div>
  );
}
