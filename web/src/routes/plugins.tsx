import { useState } from "react";
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

interface ParamField {
  key: string;
  type: string;
  default?: unknown;
  title?: string;
  minimum?: number;
  maximum?: number;
  enum?: unknown[];
}

function paramFields(schema: Record<string, unknown>): ParamField[] {
  const props = (schema.properties ?? {}) as Record<string, Record<string, unknown>>;
  return Object.entries(props).map(([key, p]) => ({
    key,
    type: (p.type as string) ?? "string",
    default: p.default,
    title: (p.title as string) ?? key,
    minimum: p.minimum as number | undefined,
    maximum: p.maximum as number | undefined,
    enum: p.enum as unknown[] | undefined,
  }));
}

function ParamEditor({
  plugin,
  onSaved,
}: {
  plugin: PluginDTO;
  onSaved: () => void;
}) {
  const qc = useQueryClient();
  const schema = (plugin.params_schema ?? {}) as Record<string, unknown>;
  const fields = paramFields(schema);
  const [values, setValues] = useState<Record<string, unknown>>({
    ...(plugin.params ?? {}),
  });
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: (v: Record<string, unknown>) =>
      api.setPluginParams(plugin.id, plugin.enabled, v),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["plugins"] });
      onSaved();
    },
    onError: (e) => setError((e as Error).message),
  });

  const set = (key: string, value: unknown) => {
    setValues((v) => ({ ...v, [key]: value }));
    setError(null);
  };

  if (fields.length === 0) return null;

  return (
    <div className="mt-3 rounded-lg border border-neutral-200 bg-neutral-50 p-3 dark:border-neutral-700 dark:bg-neutral-800/50">
      <p className="mb-2 text-xs font-medium text-neutral-500">参数（保存后对下次扫描 / 重跑生效）</p>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {fields.map((f) => {
          const val = values[f.key];
          if (f.enum) {
            return (
              <label key={f.key} className="block text-sm">
                <span className="mb-0.5 block text-neutral-500">{f.title}</span>
                <select
                  value={String(val ?? f.default ?? "")}
                  onChange={(e) => set(f.key, e.target.value)}
                  className="w-full rounded border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-600 dark:bg-neutral-800"
                >
                  {f.enum.map((opt) => (
                    <option key={String(opt)} value={String(opt)}>
                      {String(opt)}
                    </option>
                  ))}
                </select>
              </label>
            );
          }
          if (f.type === "boolean") {
            return (
              <label key={f.key} className="flex items-center justify-between gap-2 text-sm">
                <span className="text-neutral-500">{f.title}</span>
                <input
                  type="checkbox"
                  checked={Boolean(val ?? f.default ?? false)}
                  onChange={(e) => set(f.key, e.target.checked)}
                  className="h-4 w-4 accent-brand-500"
                />
              </label>
            );
          }
          const inputType =
            f.type === "integer" || f.type === "number" ? "number" : "text";
          return (
            <label key={f.key} className="block text-sm">
              <span className="mb-0.5 block text-neutral-500">{f.title}</span>
              <input
                type={inputType}
                step={f.type === "number" ? "any" : undefined}
                min={f.minimum}
                max={f.maximum}
                value={val === undefined ? "" : String(val)}
                onChange={(e) => {
                  const raw = e.target.value;
                  if (f.type === "integer") set(f.key, raw === "" ? undefined : parseInt(raw, 10));
                  else if (f.type === "number") set(f.key, raw === "" ? undefined : parseFloat(raw));
                  else set(f.key, raw);
                }}
                className="w-full rounded border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-600 dark:bg-neutral-800"
              />
            </label>
          );
        })}
      </div>
      {error && <p className="mt-2 text-xs text-red-500">{error}</p>}
      <button
        onClick={() => save.mutate(values)}
        disabled={save.isPending}
        className="mt-2 rounded bg-brand-500 px-3 py-1 text-sm font-medium text-white hover:bg-brand-600 disabled:opacity-50"
      >
        {save.isPending ? "保存中…" : "保存参数"}
      </button>
    </div>
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
  const rerun = useMutation({
    mutationFn: async (id: string) => api.rerunPlugin(id),
    onSuccess: (data, id) => {
      qc.invalidateQueries({ queryKey: ["plugins"] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
      alert(
        `「${id}」重跑完成：丢弃 ${data.dropped} 条已完成/失败任务，入队 ${data.enqueued} 条。可在「索引任务」页查看进度。`,
      );
    },
  });

  const [editing, setEditing] = useState<string | null>(null);
  const items: PluginDTO[] = data?.items ?? [];

  return (
    <div className="p-4 md:p-6">
      <h1 className="mb-1 text-xl font-semibold">插件设置</h1>
      <p className="mb-4 text-sm text-neutral-500">
        关闭插件后不再新建索引任务、队列中未运行的该插件任务暂停，并在当前进程内释放其占用内存（模型等）。磁盘上的缩略图、检测结果不会删除。「重跑」会丢弃该插件全部已完成/失败任务并整库重新入队（如调整参数后）。
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
              <th className="px-3 py-2">操作</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-neutral-100 dark:divide-neutral-800">
            {items.map((p) => (
              <tr key={p.id} className="align-top">
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
                <td className="px-3 py-2">
                  <div className="flex flex-col gap-1">
                    <button
                      onClick={() => setEditing(editing === p.id ? null : p.id)}
                      className="rounded border border-neutral-300 px-2 py-0.5 text-xs hover:bg-neutral-100 dark:border-neutral-600 dark:hover:bg-neutral-800"
                    >
                      {editing === p.id ? "收起参数" : "参数"}
                    </button>
                    <button
                      onClick={() => rerun.mutate(p.id)}
                      disabled={!p.enabled || rerun.isPending}
                      className="rounded border border-amber-300 px-2 py-0.5 text-xs text-amber-700 hover:bg-amber-50 disabled:opacity-40 dark:border-amber-700 dark:text-amber-300 dark:hover:bg-amber-950"
                    >
                      重跑
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {items.length === 0 && (
          <p className="p-6 text-center text-neutral-400">暂无插件</p>
        )}
      </div>

      {items
        .filter((p) => editing === p.id)
        .map((p) => (
          <div key={p.id} className="px-1">
            <ParamEditor plugin={p} onSaved={() => setEditing(null)} />
          </div>
        ))}

      <p className="mt-2 text-xs text-neutral-400">
        调整参数后点击「重跑」可对全库重新应用；重新开启插件后，可在「索引任务」页对未完成的文件手动重跑。
      </p>
    </div>
  );
}
