import { useEffect, useState } from "react";
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

function ParamFieldInput({
  f,
  value,
  onChange,
}: {
  f: ParamField;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  if (f.enum) {
    return (
      <label className="block text-sm">
        <span className="mb-0.5 block text-neutral-500">{f.title}</span>
        <select
          value={String(value ?? f.default ?? "")}
          onChange={(e) => onChange(e.target.value)}
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
      <label className="flex items-center justify-between gap-2 text-sm">
        <span className="text-neutral-500">{f.title}</span>
        <input
          type="checkbox"
          checked={Boolean(value ?? f.default ?? false)}
          onChange={(e) => onChange(e.target.checked)}
          className="h-4 w-4 accent-brand-500"
        />
      </label>
    );
  }
  const inputType = f.type === "integer" || f.type === "number" ? "number" : "text";
  return (
    <label className="block text-sm">
      <span className="mb-0.5 block text-neutral-500">{f.title}</span>
      <input
        type={inputType}
        step={f.type === "number" ? "any" : undefined}
        min={f.minimum}
        max={f.maximum}
        value={value === undefined ? "" : String(value)}
        onChange={(e) => {
          const raw = e.target.value;
          if (f.type === "integer") onChange(raw === "" ? undefined : parseInt(raw, 10));
          else if (f.type === "number") onChange(raw === "" ? undefined : parseFloat(raw));
          else onChange(raw);
        }}
        className="w-full rounded border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-600 dark:bg-neutral-800"
      />
    </label>
  );
}

function PluginParamModal({
  plugin,
  onClose,
}: {
  plugin: PluginDTO;
  onClose: () => void;
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
      onClose();
    },
    onError: (e) => setError((e as Error).message),
  });

  const set = (key: string, value: unknown) => {
    setValues((v) => ({ ...v, [key]: value }));
    setError(null);
  };

  // Close on ESC.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Prevent body scroll while modal is open.
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  if (fields.length === 0) {
    return (
      <div
        className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
        onClick={(e) => e.target === e.currentTarget && onClose()}
      >
        <div className="w-full max-w-md rounded-lg bg-white p-6 shadow-lg dark:bg-neutral-800">
          <h3 className="text-base font-semibold">{plugin.name}</h3>
          <p className="mt-2 text-sm text-neutral-500">该插件没有可配置参数。</p>
          <div className="mt-4 flex justify-end">
            <button
              onClick={onClose}
              className="rounded bg-neutral-100 px-3 py-1.5 text-sm font-medium hover:bg-neutral-200 dark:bg-neutral-700 dark:hover:bg-neutral-600"
            >
              关闭
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="flex max-h-[90vh] w-full max-w-2xl flex-col rounded-lg bg-white shadow-lg dark:bg-neutral-800">
        <div className="flex items-center justify-between border-b border-neutral-200 px-4 py-3 dark:border-neutral-700">
          <div>
            <h3 className="text-base font-semibold">{plugin.name}</h3>
            <p className="text-xs text-neutral-400">{plugin.id}</p>
          </div>
          <button
            onClick={onClose}
            className="rounded p-1 text-neutral-400 hover:bg-neutral-100 hover:text-neutral-600 dark:hover:bg-neutral-700"
            aria-label="关闭"
          >
            <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="overflow-y-auto p-4">
          <p className="mb-3 text-xs font-medium text-neutral-500">
            调整插件参数。保存后对下次扫描 / 重跑生效。
          </p>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {fields.map((f) => (
              <ParamFieldInput
                key={f.key}
                f={f}
                value={values[f.key]}
                onChange={(v) => set(f.key, v)}
              />
            ))}
          </div>
          {error && <p className="mt-3 text-xs text-red-500">{error}</p>}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-neutral-200 px-4 py-3 dark:border-neutral-700">
          <button
            onClick={onClose}
            className="rounded border border-neutral-300 px-4 py-1.5 text-sm font-medium hover:bg-neutral-50 dark:border-neutral-600 dark:hover:bg-neutral-700"
          >
            取消
          </button>
          <button
            onClick={() => save.mutate(values)}
            disabled={save.isPending}
            className="rounded bg-brand-500 px-4 py-1.5 text-sm font-medium text-white hover:bg-brand-600 disabled:opacity-50"
          >
            {save.isPending ? "保存中…" : "保存参数"}
          </button>
        </div>
      </div>
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

  const [editingId, setEditingId] = useState<string | null>(null);
  const items: PluginDTO[] = data?.items ?? [];
  const editingPlugin = items.find((p) => p.id === editingId) ?? null;

  return (
    <div className="p-4 md:p-6">
      <h1 className="mb-1 text-xl font-semibold">插件设置</h1>
      <p className="mb-4 text-sm text-neutral-500">
        关闭插件后不再新建索引任务、队列中未运行的该插件任务暂停，并在当前进程内释放其占用内存（模型等）。磁盘上的缩略图、检测结果不会删除。「重跑」会丢弃该插件全部已完成/失败任务并整库重新入队（如调整参数后）。
      </p>

      <div className="overflow-hidden rounded-md border border-neutral-200 dark:border-neutral-800">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-sm">
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
                    {p.description && (
                      <span className="block text-xs leading-relaxed">{p.description}</span>
                    )}
                    {p.depends_on.length > 0 && (
                      <span className="mt-0.5 block text-xs text-neutral-400">
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
                        onClick={() => setEditingId(p.id)}
                        className="rounded border border-neutral-300 px-2 py-0.5 text-xs hover:bg-neutral-100 dark:border-neutral-600 dark:hover:bg-neutral-800"
                      >
                        设置参数
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
        </div>
        {items.length === 0 && (
          <p className="p-6 text-center text-neutral-400">暂无插件</p>
        )}
      </div>

      <p className="mt-2 text-xs text-neutral-400">
        调整参数后点击「重跑」可对全库重新应用；重新开启插件后，可在「索引任务」页对未完成的文件手动重跑。
      </p>

      {editingPlugin && (
        <PluginParamModal plugin={editingPlugin} onClose={() => setEditingId(null)} />
      )}
    </div>
  );
}
