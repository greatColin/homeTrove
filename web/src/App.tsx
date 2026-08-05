import { useState } from "react";
import { NavLink, Routes, Route } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "./lib/api";
import Timeline from "./routes/timeline";
import Folders from "./routes/folders";
import Jobs from "./routes/jobs";
import Upload from "./routes/upload";
import AssetDetail from "./routes/asset_detail";
import FacetPage from "./routes/facet";
import PersonsPage from "./routes/persons";
import Plugins from "./routes/plugins";
import Search from "./routes/search";

const nav = [
  { to: "/search", label: "搜索" },
  { to: "/timeline", label: "时间轴" },
  { to: "/folders", label: "文件夹" },
  { to: "/tags", label: "标签" },
  { to: "/categories", label: "分类" },
  { to: "/faces", label: "人脸" },
  { to: "/jobs", label: "索引任务" },
  { to: "/upload", label: "上传" },
  { to: "/plugins", label: "插件设置" },
];

function Brand() {
  return (
    <div className="text-lg font-semibold tracking-tight">
      HomeTrove <span className="text-brand-500">· 家藏</span>
    </div>
  );
}

function ScanButton() {
  const [scanning, setScanning] = useState(false);
  const qc = useQueryClient();
  const scan = useMutation({
    mutationFn: api.scan,
    onSuccess: async (data) => {
      await qc.invalidateQueries({ queryKey: ["jobs"] });
      await qc.invalidateQueries({ queryKey: ["assets"] });
      setScanning(false);
      alert(`扫描完成：新增 ${data.new}，跳过 ${data.skipped}，入队 ${data.enqueued}`);
    },
  });

  return (
    <button
      onClick={() => {
        setScanning(true);
        scan.mutate();
      }}
      disabled={scanning}
      className="w-full rounded-md bg-brand-500 px-3 py-2 text-sm font-medium text-white transition hover:bg-brand-600 disabled:opacity-50"
    >
      {scanning ? "扫描中…" : "立即扫描媒体目录"}
    </button>
  );
}

function NavItems({ className }: { className: string }) {
  return (
    <>
      {nav.map((n) => (
        <NavLink
          key={n.to}
          to={n.to}
          className={({ isActive }) =>
            `${className} transition ${
              isActive
                ? "bg-brand-50 text-brand-600 dark:bg-brand-500/10 dark:text-brand-100"
                : "text-neutral-600 hover:bg-neutral-100 dark:text-neutral-400 dark:hover:bg-neutral-800"
            }`
          }
        >
          {n.label}
        </NavLink>
      ))}
    </>
  );
}

export default function App() {
  return (
    <div className="flex h-full flex-col md:flex-row">
      {/* Desktop sidebar */}
      <aside className="hidden w-56 shrink-0 flex-col border-r border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-950 md:flex">
        <div className="mb-6">
          <Brand />
        </div>
        <nav className="flex flex-col gap-1">
          <NavItems className="rounded-md px-3 py-2 text-sm" />
        </nav>
        <div className="mt-8">
          <ScanButton />
        </div>
      </aside>

      {/* Mobile top bar */}
      <header className="shrink-0 border-b border-neutral-200 bg-white px-4 py-2 dark:border-neutral-800 dark:bg-neutral-950 md:hidden">
        <div className="flex items-center justify-between">
          <Brand />
          <div className="w-32">
            <ScanButton />
          </div>
        </div>
        <nav className="mt-2 flex gap-1 overflow-x-auto pb-1">
          <NavItems className="shrink-0 rounded-md px-3 py-1.5 text-sm" />
        </nav>
      </header>

      <main className="flex-1 overflow-auto">
        <Routes>
          <Route path="/" element={<Timeline />} />
          <Route path="/search" element={<Search />} />
          <Route path="/timeline" element={<Timeline />} />
          <Route path="/folders" element={<Folders />} />
          <Route path="/tags" element={<FacetPage facet="tags" title="标签" emptyHint="按内容自动标记，点击标签查看对应文件。" />} />
          <Route path="/categories" element={<FacetPage facet="categories" title="分类" emptyHint="按内容自动归类，点击分类查看对应文件。" />} />
          <Route path="/faces" element={<PersonsPage />} />
          <Route path="/jobs" element={<Jobs />} />
          <Route path="/upload" element={<Upload />} />
          <Route path="/plugins" element={<Plugins />} />
          <Route path="/asset/:id" element={<AssetDetail />} />
        </Routes>
      </main>
    </div>
  );
}
