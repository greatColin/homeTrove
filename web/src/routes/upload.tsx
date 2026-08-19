import { useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type PluginDTO, type UploadPresetDTO } from "../lib/api";

async function jfetch<T = unknown>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/uploads${path}`, init);
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new Error(
      `${res.status}: ${typeof detail === "string" ? detail : JSON.stringify(detail)}`,
    );
  }
  return res.json() as Promise<T>;
}

interface Session {
  upload_id: string;
  chunk_size: number;
  size: number;
}

function PluginChecklist({
  plugins,
  selected,
  onChange,
}: {
  plugins: PluginDTO[];
  selected: Set<string>;
  onChange: (ids: string[]) => void;
}) {
  const allSelected = plugins.length > 0 && plugins.every((p) => selected.has(p.id));
  const toggleAll = () => {
    if (allSelected) {
      onChange([]);
    } else {
      onChange(plugins.map((p) => p.id));
    }
  };

  return (
    <div className="mt-3 rounded-lg border border-neutral-200 bg-neutral-50 p-3 dark:border-neutral-700 dark:bg-neutral-800/50">
      <div className="mb-2 flex items-center justify-between">
        <p className="text-xs font-medium text-neutral-500">选择本次上传的插件（仅显示已启用插件）</p>
        {plugins.length > 0 && (
          <button
            onClick={toggleAll}
            className="text-xs text-brand-600 hover:text-brand-700 dark:text-brand-300"
          >
            {allSelected ? "取消全选" : "全选"}
          </button>
        )}
      </div>
      <div className="flex flex-wrap gap-2">
        {plugins.map((p) => {
          const checked = selected.has(p.id);
          return (
            <label
              key={p.id}
              className={`flex items-center gap-1.5 rounded-full border px-3 py-1 text-sm cursor-pointer transition ${
                checked
                  ? "border-brand-400 bg-brand-50 text-brand-700 dark:bg-brand-950 dark:text-brand-200"
                  : "border-neutral-300 text-neutral-500 hover:border-neutral-400 dark:border-neutral-600 dark:text-neutral-400"
              }`}
            >
              <input
                type="checkbox"
                className="sr-only"
                checked={checked}
                onChange={(e) => {
                  const next = new Set(selected);
                  if (e.target.checked) next.add(p.id);
                  else next.delete(p.id);
                  onChange(Array.from(next));
                }}
              />
              {p.name}
            </label>
          );
        })}
      </div>
    </div>
  );
}

export default function Upload() {
  const fileRef = useRef<HTMLInputElement>(null);
  const qc = useQueryClient();
  const [state, setState] = useState<
    "idle" | "creating" | "uploading" | "done" | "error"
  >("idle");
  const [msg, setMsg] = useState("");
  const [progress, setProgress] = useState(0);

  const totalChunks = useRef(0);
  const doneChunks = useRef(0);

  const { data: pluginsData } = useQuery({
    queryKey: ["plugins"],
    queryFn: api.plugins,
  });
  const { data: presetsData } = useQuery({
    queryKey: ["upload-presets"],
    queryFn: api.uploadPresets,
  });

  const [selectedPreset, setSelectedPreset] = useState<number | null>(null);
  const [selectedPlugins, setSelectedPlugins] = useState<string[]>([]);
  const [presetName, setPresetName] = useState("");
  const [showSave, setShowSave] = useState(false);
  const [encrypt, setEncrypt] = useState(false);

  const vault = useQuery({
    queryKey: ["vault-status"],
    queryFn: api.vaultStatus,
  });
  const appSettings = useQuery({
    queryKey: ["settings"],
    queryFn: api.getSettings,
  });
  // The global toggle forces encryption on for every upload. We surface
  // it on the upload page so the user sees why the checkbox is locked.
  const globalEncrypt = !!appSettings.data?.encrypt_new_uploads;
  const canEncrypt = !!vault.data?.enabled && !!vault.data?.unlocked;

  const plugins: PluginDTO[] = pluginsData?.items ?? [];
  const presets: UploadPresetDTO[] = presetsData?.items ?? [];

  const applyPreset = (preset: UploadPresetDTO | null) => {
    setSelectedPreset(preset?.id ?? null);
    if (preset && preset.plugin_ids.length > 0) {
      setSelectedPlugins(preset.plugin_ids);
    } else if (preset === null && selectedPreset !== null) {
      setSelectedPlugins([]);
    }
  };

  const createPreset = useMutation({
    mutationFn: () => api.createUploadPreset(presetName.trim(), selectedPlugins),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["upload-presets"] });
      setShowSave(false);
      setPresetName("");
      alert("预设已保存");
    },
    onError: (e) => alert(`保存失败：${(e as Error).message}`),
  });

  async function upload(pluginIds: string[]) {
    const file = fileRef.current?.files?.[0];
    if (!file) {
      setMsg("请选择一个文件");
      return;
    }
    // The global toggle wins unless the user explicitly opted out for
    // this upload (which the UI only allows when the toggle is OFF).
    const effectiveEncrypted = globalEncrypt || encrypt;
    setState("creating");
    setMsg("创建上传会话…");
    try {
      const session: Session = await jfetch("", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: file.name, size: file.size, encrypted: effectiveEncrypted }),
      });
      const { upload_id, chunk_size } = session;
      totalChunks.current = Math.max(1, Math.ceil(file.size / chunk_size));
      doneChunks.current = 0;

      const info = await jfetch<{
        uploaded_chunks: number[];
        finalized: boolean;
      }>(`/${upload_id}`);
      const have = new Set(info.uploaded_chunks);
      doneChunks.current = info.finalized ? totalChunks.current : have.size;

      setState("uploading");
      setMsg("上传分片中…");

      for (let idx = 0; idx < totalChunks.current; idx++) {
        if (have.has(idx)) continue;
        const start = idx * chunk_size;
        const end = Math.min(file.size, start + chunk_size);
        const blob = file.slice(start, end);

        const putRes = await jfetch(`/${upload_id}/chunks/${idx}`, {
          method: "PUT",
          body: (() => {
            const fd = new FormData();
            fd.append("file", blob, `chunk-${idx}`);
            return fd;
          })(),
        });
        void putRes;
        doneChunks.current++;
        setProgress((doneChunks.current / totalChunks.current) * 100);
      }

      setMsg("合并校验中…");
      const complete = await jfetch<{ filename: string; staging_path: string }>(
        `/${upload_id}/complete`,
        { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" },
      );

      setMsg("分析中…");
      const params = new URLSearchParams();
      pluginIds.forEach((id) => params.append("plugin_ids", id));
      const ingestPath = `/${upload_id}/ingest${pluginIds.length > 0 ? `?${params}` : ""}`;
      const ingestRes = await fetch(`/api/uploads${ingestPath}`, { method: "POST" });
      if (!ingestRes.ok) {
        const err = await ingestRes.json().catch(() => ({ detail: "ingest failed" }));
        throw new Error(`分析失败: ${err.detail}`);
      }

      setState("done");
      setProgress(100);
      setMsg(`上传完成：${complete.filename}`);
    } catch (e) {
      setState("error");
      setMsg(e instanceof Error ? e.message : String(e));
      // If the backend rejected the upload because the vault is locked,
      // force the global VaultUnlockModal to refetch status so it auto-opens.
      // The condition it watches (configured && !unlocked) is true on
      // 423 responses, so the modal pops over the next tick.
      if (e instanceof Error && /\b423\b/.test(e.message)) {
        qc.invalidateQueries({ queryKey: ["vault-status"] });
      }
    }
  }

  return (
    <div className="p-4 md:p-6">
      <h1 className="mb-4 text-xl font-semibold">上传（分片 + 断点续传）</h1>

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <label className="text-sm text-neutral-500">预设</label>
          <select
            value={selectedPreset ?? ""}
            onChange={(e) => {
              const id = e.target.value ? Number(e.target.value) : null;
              const preset = id != null ? presets.find((p) => p.id === id) ?? null : null;
              applyPreset(preset);
            }}
            className="rounded border border-neutral-300 bg-white px-2 py-1 text-sm dark:border-neutral-600 dark:bg-neutral-800"
          >
            <option value="">— 不使用预设 —</option>
            {presets.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
                {p.is_builtin ? "（内置）" : ""}
              </option>
            ))}
          </select>
        </div>

        {selectedPlugins.length > 0 && !showSave && (
          <button
            onClick={() => setShowSave(true)}
            className="rounded border border-amber-300 px-2 py-1 text-xs text-amber-600 hover:bg-amber-50 dark:border-amber-700 dark:text-amber-300"
          >
            保存当前选择为新预设
          </button>
        )}

        {showSave && (
          <div className="flex items-center gap-2 rounded border border-amber-300 bg-amber-50 p-2 dark:border-amber-700 dark:bg-amber-950">
            <input
              value={presetName}
              onChange={(e) => setPresetName(e.target.value)}
              placeholder="新预设名称"
              className="rounded border border-neutral-300 px-2 py-0.5 text-sm dark:border-neutral-600 dark:bg-neutral-800"
              onKeyDown={(e) => {
                if (e.key === "Enter" && presetName.trim()) createPreset.mutate();
                if (e.key === "Escape") setShowSave(false);
              }}
            />
            <button
              onClick={() => createPreset.mutate()}
              disabled={!presetName.trim() || createPreset.isPending}
              className="rounded bg-amber-500 px-2 py-0.5 text-xs text-white hover:bg-amber-600 disabled:opacity-50"
            >
              {createPreset.isPending ? "保存中…" : "保存"}
            </button>
            <button
              onClick={() => setShowSave(false)}
              className="text-xs text-neutral-500 hover:text-neutral-700"
            >
              取消
            </button>
          </div>
        )}
      </div>

      {plugins.length > 0 && (
        <PluginChecklist
          plugins={plugins.filter((p) => p.enabled)}
          selected={new Set(selectedPlugins)}
          onChange={setSelectedPlugins}
        />
      )}

      <div className="mt-4 max-w-lg rounded-md border border-dashed border-neutral-300 p-6 dark:border-neutral-700">
        <input ref={fileRef} type="file" className="mb-4 block w-full text-sm" />
        {vault.data?.enabled && (
          <label className="mb-4 flex items-center gap-2 text-sm text-neutral-600 dark:text-neutral-300">
            <input
              type="checkbox"
              checked={globalEncrypt || encrypt}
              disabled={globalEncrypt || !canEncrypt}
              onChange={(e) => setEncrypt(e.target.checked)}
              className="h-4 w-4"
            />
            <span>
              加密上传到 vault
              {globalEncrypt && (
                <span className="ml-2 text-xs text-amber-600">（全局设置已开启）</span>
              )}
              {!globalEncrypt && !canEncrypt && (
                <span className="ml-2 text-xs text-amber-600">（需先解锁 vault）</span>
              )}
            </span>
          </label>
        )}
        <button
          onClick={() => upload(selectedPlugins)}
          disabled={state === "uploading" || state === "creating"}
          className="rounded-md bg-brand-500 px-4 py-2 text-sm font-medium text-white hover:bg-brand-600 disabled:opacity-50"
        >
          {state === "creating"
            ? "创建会话…"
            : state === "uploading"
              ? "上传中…"
              : "开始上传"}
        </button>

        {(state === "uploading" || state === "done") && (
          <div className="mt-4">
            <div className="mb-1 text-xs text-neutral-500">{Math.round(progress)}%</div>
            <div className="h-2 overflow-hidden rounded-full bg-neutral-200 dark:bg-neutral-700">
              <div
                className="h-full rounded-full bg-brand-500 transition-all"
                style={{ width: `${progress}%` }}
              />
            </div>
          </div>
        )}

        {msg && (
          <p
            className={`mt-3 text-sm ${
              state === "error" ? "text-red-500" : "text-neutral-500"
            }`}
          >
            {msg}
          </p>
        )}
      </div>
      <p className="mt-4 max-w-lg text-xs text-neutral-400">
        上传会按 4 MiB 分片，每片幂等（服务端已校验字节），中途断网后重新选择同一文件会
        自动从缺失的分片继续（断点续传）。完成后服务端校验总大小与哈希，并按所选插件进行分析。
      </p>
    </div>
  );
}
