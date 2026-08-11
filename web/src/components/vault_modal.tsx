import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";

interface VaultUnlockModalProps {
  /** When the parent knows the vault must be unlocked (e.g. before upload). */
  force?: boolean;
  onResolved?: () => void;
}

export default function VaultUnlockModal({ force, onResolved }: VaultUnlockModalProps) {
  const qc = useQueryClient();
  const status = useQuery({
    queryKey: ["vault-status"],
    queryFn: api.vaultStatus,
    refetchInterval: 30_000,
  });

  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const v = status.data;
    if (!v) return;
    if (v.enabled && v.configured && !v.unlocked) {
      setOpen(true);
    } else {
      setOpen(false);
      setPassword("");
    }
  }, [status.data, force]);

  const unlock = useMutation({
    mutationFn: () => api.vaultUnlock(password),
    onSuccess: async () => {
      setErr(null);
      setOpen(false);
      setPassword("");
      await qc.invalidateQueries({ queryKey: ["vault-status"] });
      onResolved?.();
    },
    onError: (e: unknown) => {
      setErr(e instanceof Error ? e.message : "解锁失败");
    },
  });

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4">
      <div className="w-full max-w-md rounded-lg border border-neutral-200 bg-white p-6 shadow-xl dark:border-neutral-800 dark:bg-neutral-950">
        <h2 className="text-lg font-semibold">解锁 vault</h2>
        <p className="mt-1 text-sm text-neutral-500">
          加密资产需要 master password 才能查看。跳过继续浏览明文资产，加密资产会显示占位图。
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
          {err && (
            <div className="text-xs text-red-500">{err}</div>
          )}
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                setPassword("");
              }}
              className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm text-neutral-600 hover:bg-neutral-50 dark:border-neutral-700 dark:text-neutral-300 dark:hover:bg-neutral-800"
            >
              跳过
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

export function VaultSetupPage() {
  const qc = useQueryClient();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const setup = useMutation({
    mutationFn: () => api.vaultSetup(password, confirm),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["vault-status"] });
      window.location.assign("/");
    },
    onError: (e: unknown) => {
      setErr(e instanceof Error ? e.message : "设置失败");
    },
  });

  return (
    <div className="mx-auto max-w-md p-8">
      <h1 className="text-2xl font-semibold">设置 vault master password</h1>
      <p className="mt-2 text-sm text-neutral-500">
        至少 12 字符。密码用于派生加密密钥，忘记后数据不可恢复。
      </p>
      <form
        className="mt-6 flex flex-col gap-3"
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
        <button
          type="submit"
          disabled={setup.isPending || !password || !confirm}
          className="rounded-md bg-brand-500 px-3 py-2 text-sm font-medium text-white transition hover:bg-brand-600 disabled:opacity-50"
        >
          {setup.isPending ? "设置中…" : "设置 vault"}
        </button>
      </form>
    </div>
  );
}