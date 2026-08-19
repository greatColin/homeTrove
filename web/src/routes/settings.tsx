import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";

export default function Settings() {
  const qc = useQueryClient();
  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: api.getSettings,
  });

  const [err, setErr] = useState<string | null>(null);

  const update = useMutation({
    mutationFn: (encrypt: boolean) => api.updateSettings({ encrypt_new_uploads: encrypt }),
    onSuccess: async () => {
      setErr(null);
      await qc.invalidateQueries({ queryKey: ["settings"] });
      await qc.invalidateQueries({ queryKey: ["vault-status"] });
    },
    onError: (e: unknown) => {
      setErr(e instanceof Error ? e.message : "操作失败");
    },
  });

  const vaultStatus = useQuery({
    queryKey: ["vault-status"],
    queryFn: api.vaultStatus,
  });

  if (settings.isLoading || vaultStatus.isLoading) {
    return (
      <div className="p-6 text-sm text-neutral-500">加载中…</div>
    );
  }
  const s = settings.data;
  const v = vaultStatus.data;
  if (!s || !v) {
    return (
      <div className="p-6 text-sm text-red-500">无法读取设置。</div>
    );
  }

  // Three branches for the toggle affordance:
  // 1. Vault disabled via env var → cannot enable; explain why.
  // 2. Vault not configured → "Open setup" instead of toggling.
  // 3. Vault configured but locked → toggle works, but next upload will
  //    require unlock (the existing VaultUnlockModal handles the prompt).
  // 4. Vault configured + unlocked → straight toggle.
  const vaultDisabled = !s.vault_enabled;
  const vaultUnconfigured = s.vault_enabled && !s.vault_configured;
  const toggleAvailable = !vaultDisabled && !vaultUnconfigured;

  const toggle = (
    <button
      role="switch"
      aria-checked={s.encrypt_new_uploads}
      disabled={!toggleAvailable || update.isPending}
      onClick={() => {
        // Enabling requires the vault to be usable; we also block here
        // so the explanation renders rather than a silent 409 from the API.
        if (!s.encrypt_new_uploads && !toggleAvailable) return;
        update.mutate(!s.encrypt_new_uploads);
      }}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition ${
        s.encrypt_new_uploads ? "bg-brand-500" : "bg-neutral-300 dark:bg-neutral-600"
      } disabled:cursor-not-allowed disabled:opacity-50`}
    >
      <span
        className={`inline-block h-4 w-4 transform rounded-full bg-white transition ${
          s.encrypt_new_uploads ? "translate-x-6" : "translate-x-1"
        }`}
      />
    </button>
  );

  return (
    <div className="p-4 md:p-6">
      <h1 className="mb-1 text-xl font-semibold">设置</h1>
      <p className="mb-4 text-sm text-neutral-500">
        全局开关影响所有新上传；已有的加密资产保持加密，关闭开关后新上传以明文存储。
      </p>

      <div className="overflow-hidden rounded-md border border-neutral-200 dark:border-neutral-800">
        <div className="flex items-start gap-4 p-4">
          <div className="flex-1">
            <div className="text-sm font-medium">新建上传自动加密</div>
            <div className="mt-1 text-xs text-neutral-500">
              开启后，每一次上传都会在写入磁盘前用 vault 密钥加密。文件内容、缩略图、关键帧都会加密。
            </div>
            {vaultDisabled && (
              <div className="mt-2 text-xs text-red-500">
                HOMETROVE_VAULT_ENABLED=false：vault 已被服务端环境变量禁用，无法启用加密。
              </div>
            )}
            {vaultUnconfigured && (
              <div className="mt-2 text-xs text-amber-600 dark:text-amber-400">
                尚未配置 vault。需要先创建 master password 才能开启加密。
              </div>
            )}
            {!vaultDisabled && !vaultUnconfigured && s.encrypt_new_uploads && !s.vault_unlocked && (
              <div className="mt-2 text-xs text-amber-600 dark:text-amber-400">
                vault 当前处于锁定状态。下一次上传会被拒，直到你解锁。
              </div>
            )}
          </div>
          <div className="pt-0.5">{toggle}</div>
        </div>
        {vaultUnconfigured && (
          <div className="border-t border-neutral-200 bg-neutral-50 p-4 dark:border-neutral-800 dark:bg-neutral-900">
            <Link
              to="/vault/setup"
              className="inline-block rounded-md bg-brand-500 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-brand-600"
            >
              创建 vault master password
            </Link>
          </div>
        )}
        {err && (
          <div className="border-t border-red-200 bg-red-50 p-3 text-xs text-red-600 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
            {err}
          </div>
        )}
      </div>

      <p className="mt-4 text-xs text-neutral-400">
        密码用于派生加密密钥，丢失后数据无法恢复。请妥善保管。
      </p>
    </div>
  );
}
