import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type PluginDTO } from "../lib/api";

const MEDIA_LABELS: Record<string, string> = {
  image: "图片",
  video: "视频",
  other: "其他",
};

function mediaLabel(s: string): string {
  return MEDIA_LABELS[s] ?? s;
}

function Toggle({
  enabled,
  pending,
  onChange,
}: {
  enabled: boolean;
  pending: boolean;
  onChange: () => void;
}) {
  return (
    <button
      role="switch"
      aria-checked={enabled}
      disabled={pending}
      onClick={onChange}
      className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition ${
        enabled ? "bg-brand-500" : "bg-neutral-300 dark:bg-neutral-600"
      } disabled:opacity-50`}
    >
      <span
        className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition ${
          enabled ? "translate-x-4" : "translate-x-0.5"
        }`}
      />
    </button>
  );
}

export default function Plugins() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["plugins"], queryFn: api.plugins });
  const toggle = useMutation({
    mutationFn: async ({ id, enabled }: { id: string; enabled: boolean }) =>
      api.setPluginEnabled(id, enabled),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["plugins"] }),
  });

  const items: PluginDTO[] = data?.items ?? [];

  return (
    <div className="p-4 md:p-6">
      <h1 className="mb-1 text-xl font-semibold">插件设置</h1>
      <p className="mb-4 text-sm text-neutral-500">
        关闭插件后不再新建索引任务、队列中未运行的该插件任务暂停，并在当前进程内释放其占用内存（模型等）。磁盘上的缩略图、检测结果不会删除。
      </p>

      <div className="overflow-hidden rounded-md border border-neutral-200 dark:border-neutral-800">
        <table className="w-full text-left text-sm">
          <thead className="bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
            <tr>
              <th className="px-3 py-2">插件</th>
              <th className="px-3 py-2">说明</th>
              <th className="px-3 py-2">适用媒体</th>
              <th className="px-3 py-2">版本</th>
              <th className="px-3 py-2">开关</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-neutral-100 dark:divide-neutral-800">
            {items.map((p) => (
              <tr key={p.id}>
                <td className="px-3 py-2">
                  <div className="font-medium">{p.name}</div>
                  <div className="font-mono text-xs text-neutral-400">{p.id}</div>
                </td>
                <td className="px-3 py-2 text-neutral-500">
                  {p.depends_on.length > 0 && (
                    <span className="block text-xs text-neutral-400">
                      依赖：{p.depends_on.join(", ")}
                    </span>
                  )}
                </td>
                <td className="px-3 py-2 text-xs text-neutral-500">
                  {p.supported_media.length > 0
                    ? p.supported_media.map(mediaLabel).join(" / ")
                    : "–"}
                </td>
                <td className="px-3 py-2 font-mono text-xs text-neutral-500">{p.version}</td>
                <td className="px-3 py-2">
                  <Toggle
                    enabled={p.enabled}
                    pending={toggle.isPending}
                    onChange={() => toggle.mutate({ id: p.id, enabled: !p.enabled })}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {items.length === 0 && (
          <p className="p-6 text-center text-neutral-400">暂无插件</p>
        )}
      </div>

      <p className="mt-2 text-xs text-neutral-400">
        关闭/重新开启后，可在「索引任务」页对未完成的文件手动重跑。
      </p>
    </div>
  );
}
