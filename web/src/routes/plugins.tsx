import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type PluginDTO, type RerunCandidate, type PluginStatusDTO, type PluginLogEntryDTO } from "../lib/api";

const MEDIA_LABELS: Record<string, string> = {
  image: "图片",
  video: "视频",
  other: "其他",
};

function mediaLabel(s: string): string {
  return MEDIA_LABELS[s] ?? s;
}

const STATUS_LABELS: Record<string, string> = {
  idle: "空闲",
  loading: "加载中",
  active: "运行中",
  stopping: "停止中",
  error: "错误",
};

const STATUS_STYLES: Record<string, string> = {
  idle: "bg-neutral-100 text-neutral-600 dark:bg-neutral-700 dark:text-neutral-300",
  loading: "bg-blue-100 text-blue-700 dark:bg-blue-500/20 dark:text-blue-300",
  active: "bg-green-100 text-green-700 dark:bg-green-500/20 dark:text-green-300",
  stopping: "bg-amber-100 text-amber-700 dark:bg-amber-500/20 dark:text-amber-300",
  error: "bg-red-100 text-red-700 dark:bg-red-500/20 dark:text-red-300",
};

function StatusBadge({ status }: { status: string }) {
  const label = STATUS_LABELS[status] ?? status;
  const style = STATUS_STYLES[status] ?? STATUS_STYLES.idle;
  return (
    <span className={`inline-flex rounded px-2 py-0.5 text-xs font-medium ${style}`}>
      {label}
    </span>
  );
}

const CATEGORY_LABELS: Record<string, string> = {
  implemented: "已实现",
  stub: "未实现（模拟）",
};

const CATEGORY_STYLES: Record<string, string> = {
  implemented: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-300",
  stub: "bg-amber-100 text-amber-700 dark:bg-amber-500/20 dark:text-amber-300",
};

function CategoryBadge({ category }: { category: string }) {
  const label = CATEGORY_LABELS[category] ?? category;
  const style = CATEGORY_STYLES[category] ?? CATEGORY_STYLES.implemented;
  return (
    <span
      className={`inline-flex rounded px-2 py-0.5 text-xs font-medium ${style}`}
      title={
        category === "stub"
          ? "该插件目前输出模拟/占位数据，或依赖一个未实现的插件"
          : "该插件从原始文件计算实际结果"
      }
    >
      {label}
    </span>
  );
}

type CategoryFilter = "all" | "implemented" | "stub";

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

function StatusModal({
  plugin,
  onClose,
}: {
  plugin: PluginDTO;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["plugin-status", plugin.id],
    queryFn: () => api.pluginStatus(plugin.id),
    refetchInterval: 2000,
  });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  const download = useMutation({
    mutationFn: () => api.downloadPluginModel(plugin.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["plugin-status", plugin.id] });
      qc.invalidateQueries({ queryKey: ["plugins"] });
    },
  });

  const status: PluginStatusDTO | undefined = data;
  const needsModel =
    plugin.id === "face.image" || plugin.id === "face.video";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="w-full max-w-md rounded-lg bg-white p-6 shadow-lg dark:bg-neutral-800">
        <div className="mb-4 flex items-center justify-between">
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

        {isLoading && <p className="text-sm text-neutral-500">加载中…</p>}
        {error && <p className="text-sm text-red-500">{(error as Error).message}</p>}
        {status && (
          <div className="space-y-3 text-sm">
            <div className="flex items-center justify-between">
              <span className="text-neutral-500">当前状态</span>
              <StatusBadge status={status.status} />
            </div>
            {status.detail && (
              <div>
                <span className="text-neutral-500">详情</span>
                <p className="mt-0.5 whitespace-pre-wrap rounded bg-neutral-50 p-2 text-xs dark:bg-neutral-900">
                  {status.detail}
                </p>
              </div>
            )}
            {status.loaded_at && (
              <div className="flex items-center justify-between">
                <span className="text-neutral-500">启动时间</span>
                <span>{new Date(status.loaded_at * 1000).toLocaleString()}</span>
              </div>
            )}
            {status.error_at && (
              <div className="flex items-center justify-between">
                <span className="text-neutral-500">错误时间</span>
                <span>{new Date(status.error_at * 1000).toLocaleString()}</span>
              </div>
            )}
            {needsModel && (
              <div className="border-t border-neutral-200 pt-3 dark:border-neutral-700">
                <button
                  onClick={() => download.mutate()}
                  disabled={download.isPending}
                  className="w-full rounded bg-brand-500 px-4 py-2 text-sm font-medium text-white hover:bg-brand-600 disabled:opacity-50"
                >
                  {download.isPending ? "下载中…" : "下载模型并重启"}
                </button>
                {download.isError && (
                  <p className="mt-2 text-xs text-red-500">
                    下载失败：{(download.error as Error).message}
                  </p>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function LogModal({
  plugin,
  onClose,
}: {
  plugin: PluginDTO;
  onClose: () => void;
}) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["plugin-logs", plugin.id],
    queryFn: () => api.pluginLogs(plugin.id, 200),
    refetchInterval: 2000,
  });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  const logs: PluginLogEntryDTO[] = data?.items ?? [];
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="flex max-h-[80vh] w-full max-w-3xl flex-col rounded-lg bg-white shadow-lg dark:bg-neutral-800">
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

        <div className="flex-1 overflow-y-auto p-4">
          {isLoading && <p className="text-sm text-neutral-500">加载中…</p>}
          {error && <p className="text-sm text-red-500">{(error as Error).message}</p>}
          {!isLoading && !error && logs.length === 0 && (
            <p className="text-sm text-neutral-500">暂无日志</p>
          )}
          <div className="space-y-1 font-mono text-xs">
            {logs.map((log, idx) => (
              <div key={idx} className="flex gap-2">
                <span className="shrink-0 text-neutral-400">
                  {new Date(log.ts * 1000).toLocaleTimeString()}
                </span>
                <span
                  className={`shrink-0 font-semibold ${
                    log.level === "ERROR"
                      ? "text-red-500"
                      : log.level === "WARN"
                      ? "text-amber-500"
                      : "text-blue-500"
                  }`}
                >
                  {log.level}
                </span>
                <span className="break-all text-neutral-700 dark:text-neutral-200">{log.message}</span>
              </div>
            ))}
            <div ref={bottomRef} />
          </div>
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


  const [editingId, setEditingId] = useState<string | null>(null);
  const [rerunningId, setRerunningId] = useState<string | null>(null);
  const [statusId, setStatusId] = useState<string | null>(null);
  const [logsId, setLogsId] = useState<string | null>(null);
  const [categoryFilter, setCategoryFilter] = useState<CategoryFilter>("all");
  const items: PluginDTO[] = data?.items ?? [];
  const editingPlugin = items.find((p) => p.id === editingId) ?? null;
  const rerunningPlugin = items.find((p) => p.id === rerunningId) ?? null;
  const statusPlugin = items.find((p) => p.id === statusId) ?? null;
  const logsPlugin = items.find((p) => p.id === logsId) ?? null;
  const filteredItems =
    categoryFilter === "all" ? items : items.filter((p) => p.category === categoryFilter);
  const categoryCounts = items.reduce<Record<string, number>>(
    (acc, p) => {
      acc[p.category] = (acc[p.category] ?? 0) + 1;
      return acc;
    },
    {},
  );

  return (
    <div className="p-4 md:p-6">
      <h1 className="mb-1 text-xl font-semibold">插件设置</h1>
      <p className="mb-4 text-sm text-neutral-500">
        关闭插件后不再新建索引任务、队列中未运行的该插件任务暂停，并在当前进程内释放其占用内存（模型等）。磁盘上的缩略图、检测结果不会删除。「重跑」会丢弃该插件全部已完成/失败任务并整库重新入队（如调整参数后）。
      </p>

      <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
        <span className="text-neutral-500">类型筛选：</span>
        {(
          [
            { key: "all", label: "全部" },
            { key: "implemented", label: "已实现" },
            { key: "stub", label: "未实现（模拟）" },
          ] as { key: CategoryFilter; label: string }[]
        ).map((opt) => {
          const active = categoryFilter === opt.key;
          const count =
            opt.key === "all"
              ? items.length
              : categoryCounts[opt.key] ?? 0;
          return (
            <button
              key={opt.key}
              onClick={() => setCategoryFilter(opt.key)}
              className={`rounded border px-3 py-1 text-xs transition ${
                active
                  ? "border-brand-500 bg-brand-500 text-white"
                  : "border-neutral-300 hover:bg-neutral-100 dark:border-neutral-600 dark:hover:bg-neutral-800"
              }`}
            >
              {opt.label} ({count})
            </button>
          );
        })}
      </div>

      <div className="overflow-hidden rounded-md border border-neutral-200 dark:border-neutral-800">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead className="bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
              <tr>
                <th className="px-3 py-2">插件</th>
                <th className="px-3 py-2">类别</th>
                <th className="px-3 py-2">说明</th>
                <th className="px-3 py-2">适用媒体</th>
                <th className="px-3 py-2">版本</th>
                <th className="px-3 py-2">状态</th>
                <th className="px-3 py-2">开关</th>
                <th className="px-3 py-2">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-neutral-100 dark:divide-neutral-800">
              {filteredItems.map((p) => (
                <tr key={p.id} className="align-top">
                  <td className="px-3 py-2">
                    <div className="font-medium">{p.name}</div>
                    <div className="font-mono text-xs text-neutral-400">{p.id}</div>
                  </td>
                  <td className="px-3 py-2">
                    <CategoryBadge category={p.category} />
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
                    <StatusBadge status={p.status} />
                    {p.status_detail && (
                      <span className="mt-0.5 block max-w-[12rem] truncate text-xs text-neutral-400" title={p.status_detail}>
                        {p.status_detail}
                      </span>
                    )}
                  </td>
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
                        onClick={() => setStatusId(p.id)}
                        className="rounded border border-neutral-300 px-2 py-0.5 text-xs hover:bg-neutral-100 dark:border-neutral-600 dark:hover:bg-neutral-800"
                      >
                        状态
                      </button>
                      <button
                        onClick={() => setLogsId(p.id)}
                        className="rounded border border-neutral-300 px-2 py-0.5 text-xs hover:bg-neutral-100 dark:border-neutral-600 dark:hover:bg-neutral-800"
                      >
                        日志
                      </button>
                      <button
                        onClick={() => setEditingId(p.id)}
                        className="rounded border border-neutral-300 px-2 py-0.5 text-xs hover:bg-neutral-100 dark:border-neutral-600 dark:hover:bg-neutral-800"
                      >
                        设置参数
                      </button>
                      <button
                        onClick={() => setRerunningId(p.id)}
                        disabled={!p.enabled}
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
        {items.length > 0 && filteredItems.length === 0 && (
          <p className="p-6 text-center text-neutral-400">
            当前筛选下没有插件（共 {items.length} 个）。
          </p>
        )}
      </div>

      <p className="mt-2 text-xs text-neutral-400">
        调整参数后点击「重跑」可对全库重新应用；重新开启插件后，可在「索引任务」页对未完成的文件手动重跑。
      </p>

      {editingPlugin && (
        <PluginParamModal plugin={editingPlugin} onClose={() => setEditingId(null)} />
      )}
      {rerunningPlugin && (
        <RerunScopeModal plugin={rerunningPlugin} onClose={() => setRerunningId(null)} />
      )}
      {statusPlugin && <StatusModal plugin={statusPlugin} onClose={() => setStatusId(null)} />}
      {logsPlugin && <LogModal plugin={logsPlugin} onClose={() => setLogsId(null)} />}
    </div>
  );
}

function useDebouncedValue<T>(value: T, delay = 200): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return v;
}

function RerunScopeModal({
  plugin,
  onClose,
}: {
  plugin: PluginDTO;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [mode, setMode] = useState<"all" | "selected">("selected");
  const [q, setQ] = useState("");
  const debouncedQ = useDebouncedValue(q, 250);
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [candidates, setCandidates] = useState<RerunCandidate[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const limit = 50;

  const fetchCandidates = async (query: string, off: number, append: boolean) => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.rerunCandidates(plugin.id, query, off, limit);
      setTotal(res.total);
      setCandidates((prev) => (append ? [...prev, ...res.items] : res.items));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (mode !== "selected") return;
    setOffset(0);
    void fetchCandidates(debouncedQ, 0, false);
  }, [debouncedQ, mode, plugin.id]);

  useEffect(() => {
    if (mode !== "all") return;
    setOffset(0);
    void fetchCandidates("", 0, false);
  }, [mode, plugin.id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  const toggleOne = (id: number) => {
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAllVisible = () => {
    const visible = new Set(candidates.map((c) => c.asset_id));
    const allSelected = [...visible].every((id) => selected.has(id));
    setSelected((s) => {
      const next = new Set(s);
      if (allSelected) {
        visible.forEach((id) => next.delete(id));
      } else {
        visible.forEach((id) => next.add(id));
      }
      return next;
    });
  };

  const selectedCount = mode === "all" ? total : selected.size;

  const rerun = useMutation({
    mutationFn: () =>
      mode === "all"
        ? api.rerunPlugin(plugin.id)
        : api.rerunSelected(plugin.id, Array.from(selected)),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["plugins"] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
      onClose();
      alert(
        `「${plugin.id}」重跑完成：丢弃 ${data.dropped} 条已完成/失败任务，入队 ${data.enqueued} 条。可在「索引任务」页查看进度。`,
      );
    },
    onError: (e) => setError((e as Error).message),
  });

  const mediaHint = useMemo(() => {
    if (plugin.supported_media.length === 0) return "";
    return `（仅 ${plugin.supported_media.map(mediaLabel).join(" / ")}）`;
  }, [plugin.supported_media]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 sm:items-center sm:p-4"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="flex h-[90vh] w-full flex-col rounded-t-lg bg-white shadow-lg sm:h-auto sm:max-h-[85vh] sm:max-w-2xl sm:rounded-lg dark:bg-neutral-800">
        <div className="flex items-center justify-between border-b border-neutral-200 px-4 py-3 dark:border-neutral-700">
          <div>
            <h3 className="text-base font-semibold">重跑：{plugin.name}</h3>
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

        <div className="flex items-center gap-2 border-b border-neutral-200 px-4 py-2 dark:border-neutral-700">
          <button
            onClick={() => setMode("all")}
            className={`rounded px-3 py-1 text-sm ${
              mode === "all"
                ? "bg-brand-100 text-brand-700 dark:bg-brand-500/20 dark:text-brand-300"
                : "text-neutral-600 hover:bg-neutral-100 dark:text-neutral-300 dark:hover:bg-neutral-700"
            }`}
          >
            全部文件{mediaHint}
          </button>
          <button
            onClick={() => setMode("selected")}
            className={`rounded px-3 py-1 text-sm ${
              mode === "selected"
                ? "bg-brand-100 text-brand-700 dark:bg-brand-500/20 dark:text-brand-300"
                : "text-neutral-600 hover:bg-neutral-100 dark:text-neutral-300 dark:hover:bg-neutral-700"
            }`}
          >
            选择文件
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {mode === "selected" && (
            <>
              <div className="mb-3 flex items-center gap-2">
                <input
                  type="text"
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  placeholder="搜索文件名或路径…"
                  className="flex-1 rounded border border-neutral-300 bg-white px-3 py-1.5 text-sm dark:border-neutral-600 dark:bg-neutral-800"
                />
                <button
                  onClick={toggleAllVisible}
                  disabled={candidates.length === 0}
                  className="rounded border border-neutral-300 px-3 py-1.5 text-sm hover:bg-neutral-50 disabled:opacity-40 dark:border-neutral-600 dark:hover:bg-neutral-700"
                >
                  全选/取消
                </button>
              </div>

              {loading && candidates.length === 0 && (
                <p className="py-4 text-center text-sm text-neutral-400">加载中…</p>
              )}

              <div className="space-y-1">
                {candidates.map((c) => (
                  <label
                    key={c.asset_id}
                    className="flex cursor-pointer items-center gap-3 rounded border border-neutral-100 p-2 hover:bg-neutral-50 dark:border-neutral-700 dark:hover:bg-neutral-700/50"
                  >
                    <input
                      type="checkbox"
                      checked={selected.has(c.asset_id)}
                      onChange={() => toggleOne(c.asset_id)}
                      className="h-4 w-4 accent-brand-500"
                    />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">{c.filename}</div>
                      <div className="truncate text-xs text-neutral-400">{c.path}</div>
                    </div>
                    <span className="text-xs text-neutral-500">{mediaLabel(c.media_type)}</span>
                  </label>
                ))}
              </div>

              {candidates.length < total && (
                <div className="mt-3 text-center">
                  <button
                    onClick={() => {
                      const next = offset + limit;
                      setOffset(next);
                      void fetchCandidates(debouncedQ, next, true);
                    }}
                    disabled={loading}
                    className="rounded border border-neutral-300 px-4 py-1.5 text-sm hover:bg-neutral-50 disabled:opacity-50 dark:border-neutral-600 dark:hover:bg-neutral-700"
                  >
                    {loading ? "加载中…" : `加载更多 (${candidates.length}/${total})`}
                  </button>
                </div>
              )}

              {candidates.length === 0 && !loading && (
                <p className="py-6 text-center text-sm text-neutral-400">未找到匹配文件</p>
              )}
            </>
          )}

          {mode === "all" && (
            <div className="py-6 text-center">
              <p className="text-sm text-neutral-500">
                将对全库符合条件的文件重新入队{mediaHint}。
              </p>
              <p className="mt-2 text-2xl font-semibold">{total}</p>
              <p className="text-xs text-neutral-400">个文件将被重跑</p>
              {loading && <p className="mt-2 text-xs text-neutral-400">统计中…</p>}
            </div>
          )}

          {error && <p className="mt-3 text-xs text-red-500">{error}</p>}
        </div>

        <div className="flex items-center justify-between border-t border-neutral-200 px-4 py-3 dark:border-neutral-700">
          <span className="text-sm text-neutral-500">
            已选 <span className="font-semibold text-neutral-700 dark:text-neutral-200">{selectedCount}</span> 个文件
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={onClose}
              className="rounded border border-neutral-300 px-4 py-1.5 text-sm font-medium hover:bg-neutral-50 dark:border-neutral-600 dark:hover:bg-neutral-700"
            >
              取消
            </button>
            <button
              onClick={() => rerun.mutate()}
              disabled={rerun.isPending || selectedCount === 0}
              className="rounded bg-amber-500 px-4 py-1.5 text-sm font-medium text-white hover:bg-amber-600 disabled:opacity-50"
            >
              {rerun.isPending ? "提交中…" : "确认重跑"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
