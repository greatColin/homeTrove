import type { ReactNode } from "react";

export const PLUGIN_LABELS: Record<string, string> = {
  "basic.info": "基本信息",
  "thumbnail": "缩略图",
  "exif": "EXIF 元数据",
  "basic.scene_detect": "场景切分",
  "mock.tags": "标签（模拟）",
  "mock.category": "分类（模拟）",
  "mock.faces": "人脸（模拟）",
};

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "–";
  if (typeof v === "boolean") return v ? "是" : "否";
  if (typeof v === "number") {
    // Integer-like values stay as ints; time-ish keys formatted below.
    if (Number.isInteger(v)) return String(v);
    return String(Math.round(v * 1000) / 1000);
  }
  if (typeof v === "string") return v;
  if (Array.isArray(v)) return v.join("、");
  return JSON.stringify(v);
}

function Row({ k, v }: { k: string; v: unknown }) {
  return (
    <div className="flex justify-between gap-4 border-b border-neutral-100 py-1.5 text-sm dark:border-neutral-800">
      <span className="shrink-0 text-neutral-500">{k}</span>
      <span className="break-all text-right">{formatValue(v)}</span>
    </div>
  );
}

function Nested({ k, v }: { k: string; v: unknown }) {
  const isObj = v !== null && typeof v === "object" && !Array.isArray(v);
  const isArr =
    Array.isArray(v) &&
    v.length > 0 &&
    v.every((x) => x !== null && typeof x === "object");
  if (isObj || isArr) {
    return (
      <details className="border-b border-neutral-100 py-1.5 dark:border-neutral-800">
        <summary className="cursor-pointer text-sm text-neutral-500 hover:text-brand-500">
          {k}（{isArr ? `${v.length} 项` : isObj ? Object.keys(v as object).length + " 字段" : ""}）
        </summary>
        <div className="mt-1 pl-3">
          {isArr
            ? (v as unknown[]).map((item, i) => (
                <div key={i} className="border-l-2 border-neutral-100 pl-2 dark:border-neutral-800">
                  {Object.entries(item as object).map(([ik, iv]) => (
                    <Row key={ik} k={ik} v={iv} />
                  ))}
                </div>
              ))
            : Object.entries(v as object).map(([ik, iv]) => (
                <Row key={ik} k={ik} v={iv} />
              ))}
        </div>
      </details>
    );
  }
  return <Row k={k} v={v} />;
}

export function PluginDataBlock({
  pluginId,
  data,
}: {
  pluginId: string;
  data: Record<string, unknown>;
}) {
  return (
    <div>
      {Object.entries(data).map(([k, v]) => (
        <Nested key={k} k={k} v={v} />
      ))}
      {Object.keys(data).length === 0 && (
        <p className="py-2 text-sm text-neutral-400">无数据</p>
      )}
    </div>
  );
}

export function PluginBadge({ pluginId }: { pluginId: string }) {
  return (
    <span className="rounded bg-neutral-100 px-1.5 py-0.5 font-mono text-[10px] text-neutral-500 dark:bg-neutral-800">
      {PLUGIN_LABELS[pluginId] ?? pluginId}
    </span>
  );
}

export function FacetChips({
  facets,
  selected,
  onSelect,
}: {
  facets: Record<string, number>;
  selected?: string;
  onSelect: (v: string) => void;
}) {
  const entries: [string, number][] = Object.entries(facets).sort(
    (a, b) => b[1] - a[1],
  );
  if (entries.length === 0) {
    return <p className="text-sm text-neutral-400">暂无数据</p>;
  }
  return (
    <div className="flex flex-wrap gap-2">
      {entries.map(([name, count]) => (
        <button
          key={name}
          onClick={() => onSelect(name)}
          className={`rounded-full px-3 py-1 text-sm transition ${
            selected === name
              ? "bg-brand-500 text-white"
              : "bg-neutral-100 text-neutral-700 hover:bg-brand-50 dark:bg-neutral-800 dark:text-neutral-300 dark:hover:bg-brand-500/10"
          }`}
        >
          {name}
          <span className={`ml-1 text-xs ${selected === name ? "text-white/70" : "text-neutral-400"}`}>
            {count}
          </span>
        </button>
      ))}
    </div>
  );
}

export function PluginSection({
  title,
  children,
}: {
  title: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="rounded-md border border-neutral-200 p-4 dark:border-neutral-800">
      {children}
    </section>
  );
}
