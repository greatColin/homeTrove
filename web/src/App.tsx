import { lazy, Suspense, useState } from "react";
import { NavLink, Routes, Route } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./lib/api";
import Timeline from "./routes/timeline";
import Folders from "./routes/folders";
import Jobs from "./routes/jobs";
import Upload from "./routes/upload";
import AssetDetail from "./routes/asset_detail";
import FacetPage from "./routes/facet";
import PersonsPage from "./routes/persons";
import ClusterDetailPage from "./routes/cluster_detail";
import Plugins from "./routes/plugins";
import Search from "./routes/search";
import Albums from "./routes/albums";
import Settings from "./routes/settings";
import Trash from "./routes/trash";
import SharedAlbum from "./routes/shared_album";
import VaultUnlockModal, { VaultSetupPage } from "./components/vault_modal";

const Places = lazy(() => import("./routes/places"));

interface NavItem {
  to: string;
  label: string;
  icon?: string;
}

interface NavGroup {
  label: string;
  items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    label: "发现",
    items: [
      { to: "/search", label: "搜索" },
      { to: "/timeline", label: "时间轴" },
      { to: "/albums", label: "相册" },
      { to: "/folders", label: "文件夹" },
      { to: "/places", label: "地点" },
      { to: "/tags", label: "标签" },
      { to: "/categories", label: "分类" },
    ],
  },
  {
    label: "人物",
    items: [
      { to: "/faces", label: "人物" },
    ],
  },
  {
    label: "任务",
    items: [
      { to: "/jobs", label: "索引任务" },
    ],
  },
  {
    label: "系统",
    items: [
      { to: "/upload", label: "上传" },
      { to: "/plugins", label: "插件设置" },
      { to: "/trash", label: "回收站" },
      { to: "/settings", label: "设置" },
    ],
  },
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
      alert(`扫描完成：新增 ${data.new}，跳过 ${data.skipped}${data.note ? "，" + data.note : "。\n请到「队列管理」页面勾选插件后手动入队。" }`);
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

function VaultLockButton() {
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["vault-status"], queryFn: api.vaultStatus });
  const lock = useMutation({
    mutationFn: api.vaultLock,
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["vault-status"] });
    },
  });

  if (!status.data?.enabled || !status.data?.unlocked) return null;
  return (
    <button
      onClick={() => lock.mutate()}
      disabled={lock.isPending}
      className="mt-2 w-full rounded-md border border-neutral-300 px-3 py-2 text-xs text-neutral-600 transition hover:bg-neutral-100 disabled:opacity-50 dark:border-neutral-700 dark:text-neutral-300 dark:hover:bg-neutral-800"
    >
      {lock.isPending ? "锁定中…" : "锁定 vault"}
    </button>
  );
}

function VaultSetupModal({
  onClose,
  onSuccess,
}: {
  onClose: () => void;
  onSuccess: () => void;
}) {
  const qc = useQueryClient();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const setup = useMutation({
    mutationFn: () => api.vaultSetup(password, confirm),
    onSuccess: async () => {
      setErr(null);
      await qc.invalidateQueries({ queryKey: ["vault-status"] });
      await qc.invalidateQueries({ queryKey: ["settings"] });
      onSuccess();
      onClose();
    },
    onError: (e: unknown) => {
      setErr(e instanceof Error ? e.message : "设置失败");
    },
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-md rounded-lg border border-neutral-200 bg-white p-6 shadow-xl dark:border-neutral-800 dark:bg-neutral-950">
        <h2 className="text-lg font-semibold">设置 vault master password</h2>
        <p className="mt-1 text-sm text-neutral-500">
          至少 12 字符。密码用于派生加密密钥，忘记后数据不可恢复。
        </p>
        <form
          className="mt-4 flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            setErr(null);
            if (password.length < 12) {
              setErr("密码至少 12 字符");
              return;
            }
            if (password !== confirm) {
              setErr("两次输入不一致");
              return;
            }
            setup.mutate();
          }}
        >
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Master password"
            autoFocus
            className="rounded-md border border-neutral-300 px-3 py-2 text-sm dark:border-neutral-700 dark:bg-neutral-900"
          />
          <input
            type="password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            placeholder="再次输入"
            className="rounded-md border border-neutral-300 px-3 py-2 text-sm dark:border-neutral-700 dark:bg-neutral-900"
          />
          {err && <div className="text-xs text-red-500">{err}</div>}
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm text-neutral-600 hover:bg-neutral-50 dark:border-neutral-700 dark:text-neutral-300 dark:hover:bg-neutral-800"
            >
              取消
            </button>
            <button
              type="submit"
              disabled={setup.isPending || !password || !confirm}
              className="rounded-md bg-brand-500 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-brand-600 disabled:opacity-50"
            >
              {setup.isPending ? "设置中…" : "设置 vault"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function VaultUnlockModalForEncrypt({
  onClose,
  onSuccess,
}: {
  onClose: () => void;
  onSuccess: () => void;
}) {
  const qc = useQueryClient();
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const unlock = useMutation({
    mutationFn: () => api.vaultUnlock(password),
    onSuccess: async () => {
      setErr(null);
      setPassword("");
      await qc.invalidateQueries({ queryKey: ["vault-status"] });
      onSuccess();
      onClose();
    },
    onError: (e: unknown) => {
      setErr(e instanceof Error ? e.message : "解锁失败");
    },
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-md rounded-lg border border-neutral-200 bg-white p-6 shadow-xl dark:border-neutral-800 dark:bg-neutral-950">
        <h2 className="text-lg font-semibold">解锁 vault</h2>
        <p className="mt-1 text-sm text-neutral-500">
          需要解锁 vault 才能开启加密功能。
        </p>
        <form
          className="mt-4 flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            setErr(null);
            unlock.mutate();
          }}
        >
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Master password"
            autoFocus
            className="rounded-md border border-neutral-300 px-3 py-2 text-sm dark:border-neutral-700 dark:bg-neutral-900"
          />
          {err && <div className="text-xs text-red-500">{err}</div>}
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm text-neutral-600 hover:bg-neutral-50 dark:border-neutral-700 dark:text-neutral-300 dark:hover:bg-neutral-800"
            >
              取消
            </button>
            <button
              type="submit"
              disabled={unlock.isPending || password.length === 0}
              className="rounded-md bg-brand-500 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-brand-600 disabled:opacity-50"
            >
              {unlock.isPending ? "解锁中…" : "解锁"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function EncryptToggle() {
  const qc = useQueryClient();
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.getSettings });
  const vaultStatus = useQuery({ queryKey: ["vault-status"], queryFn: api.vaultStatus });
  const [showSetup, setShowSetup] = useState(false);
  const [showUnlock, setShowUnlock] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const updateSettings = useMutation({
    mutationFn: (encrypt: boolean) => api.updateSettings({ encrypt_new_uploads: encrypt }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["settings"] });
      await qc.invalidateQueries({ queryKey: ["vault-status"] });
      setErr(null);
    },
    onError: (e: unknown) => {
      setErr(e instanceof Error ? e.message : "操作失败");
    },
  });

  if (settings.isLoading || vaultStatus.isLoading || !settings.data || !vaultStatus.data) {
    return null;
  }

  const s = settings.data;
  const v = vaultStatus.data;
  const encrypting = s.encrypt_new_uploads ?? false;

  if (!v.enabled) return null;

  const toggle = (
    <button
      role="switch"
      aria-checked={encrypting}
      disabled={updateSettings.isPending}
      onClick={() => {
        setErr(null);
        if (!encrypting) {
          if (!v.configured) {
            setShowSetup(true);
            return;
          }
          if (!v.unlocked) {
            setShowUnlock(true);
            return;
          }
        }
        updateSettings.mutate(!encrypting);
      }}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition ${
        encrypting ? "bg-brand-500" : "bg-neutral-300 dark:bg-neutral-600"
      } disabled:opacity-50`}
    >
      <span
        className={`inline-block h-4 w-4 transform rounded-full bg-white transition ${
          encrypting ? "translate-x-6" : "translate-x-1"
        }`}
      />
    </button>
  );

  return (
    <>
      <div className="mt-2 flex items-center justify-between rounded-md border border-neutral-200 px-3 py-2 dark:border-neutral-800">
        <span className="text-xs text-neutral-600 dark:text-neutral-300">新建上传自动加密</span>
        {toggle}
      </div>
      {err && <p className="mt-1 text-xs text-red-500">{err}</p>}
      {showSetup && (
        <VaultSetupModal
          onClose={() => setShowSetup(false)}
          onSuccess={() => updateSettings.mutate(true)}
        />
      )}
      {showUnlock && (
        <VaultUnlockModalForEncrypt
          onClose={() => setShowUnlock(false)}
          onSuccess={() => updateSettings.mutate(true)}
        />
      )}
    </>
  );
}

function NavItems({ className }: { className: string }) {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  return (
    <>
      {NAV_GROUPS.map((group) => {
        const isCollapsed = collapsed[group.label] ?? false;
        return (
          <div key={group.label} className="mb-1">
            <button
              onClick={() => setCollapsed((prev) => ({ ...prev, [group.label]: !prev[group.label] }))}
              className={`flex w-full items-center justify-between rounded-md px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-neutral-400 hover:text-neutral-600 dark:hover:text-neutral-300 ${className}`}
            >
              <span>{group.label}</span>
              <span className={`transition-transform ${isCollapsed ? "-rotate-90" : ""}`}>
                <svg className="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                </svg>
              </span>
            </button>
            {!isCollapsed && (
              <div className="ml-2 mt-0.5 flex flex-col gap-0.5">
                {group.items.map((n) => (
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
              </div>
            )}
          </div>
        );
      })}
    </>
  );
}

export default function App() {
  return (
    <div className="flex h-full flex-col md:flex-row">
      {/* Desktop sidebar */}
      <aside className="hidden w-56 shrink-0 flex-col border-r border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-950 md:flex">
        <div className="mb-4">
          <Brand />
        </div>
        <nav className="flex flex-col gap-0.5 overflow-y-auto">
          <NavItems className="rounded-md px-3 py-2 text-sm" />
        </nav>
        <div className="mt-4">
          <ScanButton />
          <EncryptToggle />
          <VaultLockButton />
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
          <Route path="/albums" element={<Albums />} />
          <Route path="/albums/:id" element={<Albums />} />
          <Route path="/folders" element={<Folders />} />
          <Route
            path="/places"
            element={
              <Suspense fallback={<div className="p-6 text-sm text-neutral-500">加载地图…</div>}>
                <Places />
              </Suspense>
            }
          />
          <Route path="/tags" element={<FacetPage facet="tags" title="标签" emptyHint="按内容自动标记，点击标签查看对应文件。" />} />
          <Route path="/categories" element={<FacetPage facet="categories" title="分类" emptyHint="按内容自动归类，点击分类查看对应文件。" />} />
          <Route path="/faces" element={<PersonsPage />} />
          <Route path="/persons" element={<PersonsPage />} />
          <Route path="/persons/:personId" element={<PersonsPage />} />
          <Route path="/clusters/:clusterId" element={<ClusterDetailPage />} />
          <Route path="/jobs" element={<Jobs />} />
          <Route path="/upload" element={<Upload />} />
          <Route path="/plugins" element={<Plugins />} />
          <Route path="/trash" element={<Trash />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/share/:token" element={<SharedAlbum />} />
          <Route path="/asset/:id" element={<AssetDetail />} />
          <Route path="/vault/setup" element={<VaultSetupPage />} />
        </Routes>
      </main>
      <VaultUnlockModal />
    </div>
  );
}
