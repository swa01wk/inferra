import { useState, useEffect, useCallback } from "react";
import { Plus, Trash2, Eye, EyeOff } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { makeAdminClient } from "../../api/client";
import { Badge } from "../../components/shared/Badge";
import { CopyButton } from "../../components/shared/CopyButton";
import { ErrorBanner } from "../../components/shared/ErrorBanner";
import type { ApiKey } from "../../api/types";

export function KeysPage() {
  const auth = useAuth();
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showModal, setShowModal] = useState(false);
  const [newName, setNewName] = useState("");
  const [newExpiresAt, setNewExpiresAt] = useState("");
  const [createdSecret, setCreatedSecret] = useState<string | null>(null);
  const [secretVisible, setSecretVisible] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const fetchKeys = useCallback(async () => {
    if (!auth.adminKey) return;
    setLoading(true);
    setError(null);
    try {
      const client = makeAdminClient(auth.gatewayUrl, auth.adminKey);
      const res = await client.get("/api-keys");
      setKeys(res.data.api_keys);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [auth.gatewayUrl, auth.adminKey]);

  useEffect(() => { fetchKeys(); }, [fetchKeys]);

  const createKey = async () => {
    if (!newName.trim() || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const client = makeAdminClient(auth.gatewayUrl, auth.adminKey);
      const payload: Record<string, unknown> = { name: newName.trim() };
      if (newExpiresAt) payload.expires_at = newExpiresAt;
      const res = await client.post("/api-keys", payload);
      setCreatedSecret(res.data.secret);
      setNewName("");
      setNewExpiresAt("");
      await fetchKeys();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  const revokeKey = async (id: string) => {
    if (!confirm("Revoke this key? This cannot be undone.")) return;
    try {
      const client = makeAdminClient(auth.gatewayUrl, auth.adminKey);
      await client.delete(`/api-keys/${id}`);
      await fetchKeys();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="mx-auto max-w-5xl px-4 py-8">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">API Keys</h1>
          <p className="mt-0.5 text-sm text-slate-400">Manage inference keys for your organization</p>
        </div>
        <button
          onClick={() => { setShowModal(true); setCreatedSecret(null); }}
          className="flex items-center gap-2 rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500"
        >
          <Plus className="h-4 w-4" /> New Key
        </button>
      </div>

      {!auth.adminKey && (
        <div className="rounded-lg border border-amber-800/50 bg-amber-950/30 p-4 text-sm text-amber-300">
          Set your Admin Key in Settings (⚙️) to manage API keys.
        </div>
      )}

      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      {loading ? (
        <div className="text-center py-12 text-slate-500 text-sm">Loading…</div>
      ) : (
        <div className="rounded-xl border border-slate-800 bg-slate-900 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-800 text-left">
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Name</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Prefix</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Type</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Status</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Expires</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Created</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400"></th>
              </tr>
            </thead>
            <tbody>
              {keys.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-4 py-8 text-center text-slate-500">No keys found</td>
                </tr>
              ) : keys.map(k => (
                <tr key={k.id} className="border-b border-slate-800/50 last:border-0 hover:bg-slate-800/30">
                  <td className="px-4 py-3 font-medium text-slate-200">{k.name}</td>
                  <td className="px-4 py-3 font-mono text-slate-400 text-xs">{k.key_prefix}…</td>
                  <td className="px-4 py-3">
                    <span className={`text-xs ${k.is_admin ? "text-amber-400" : "text-sky-400"}`}>
                      {k.is_admin ? "admin" : "inference"}
                    </span>
                  </td>
                  <td className="px-4 py-3"><Badge status={k.status} /></td>
                  <td className="px-4 py-3 text-slate-400 text-xs">
                    {k.expires_at ? new Date(k.expires_at).toLocaleDateString() : "Never"}
                  </td>
                  <td className="px-4 py-3 text-slate-400 text-xs">
                    {k.created_at ? new Date(k.created_at).toLocaleDateString() : "—"}
                  </td>
                  <td className="px-4 py-3">
                    {k.status === "active" && !k.is_admin && (
                      <button onClick={() => revokeKey(k.id)}
                        className="text-red-400 hover:text-red-300 p-1">
                        <Trash2 className="h-4 w-4" />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* New Key Modal */}
      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-xl border border-slate-700 bg-slate-900 p-6 shadow-2xl">
            <h2 className="mb-5 text-lg font-semibold text-slate-100">
              {createdSecret ? "Key Created" : "New API Key"}
            </h2>

            {createdSecret ? (
              <div className="space-y-4">
                <div className="rounded-lg border border-amber-700/50 bg-amber-950/30 p-3 text-xs text-amber-300">
                  ⚠️ Copy this key now — it will not be shown again.
                </div>
                <div className="rounded-lg border border-slate-700 bg-slate-800 p-3">
                  <div className="flex items-center justify-between gap-2 mb-2">
                    <label className="text-xs text-slate-400">Secret key</label>
                    <div className="flex gap-2">
                      <button onClick={() => setSecretVisible(v => !v)} className="text-slate-400 hover:text-slate-200">
                        {secretVisible ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                      </button>
                      <CopyButton text={createdSecret} />
                    </div>
                  </div>
                  <p className="font-mono text-xs text-slate-300 break-all">
                    {secretVisible ? createdSecret : createdSecret.replace(/./g, "•")}
                  </p>
                </div>
                <button onClick={() => { setShowModal(false); setCreatedSecret(null); setSecretVisible(false); }}
                  className="w-full rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500">
                  Done
                </button>
              </div>
            ) : (
              <div className="space-y-4">
                <div>
                  <label className="mb-1.5 block text-xs font-medium text-slate-400">Name</label>
                  <input
                    autoFocus
                    value={newName}
                    onChange={e => setNewName(e.target.value)}
                    onKeyDown={e => { if (e.key === "Enter") createKey(); }}
                    className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:border-sky-500 focus:outline-none"
                    placeholder="e.g. dev-key"
                  />
                </div>
                <div>
                  <label className="mb-1.5 block text-xs font-medium text-slate-400">Expires at (optional)</label>
                  <input type="datetime-local"
                    value={newExpiresAt}
                    onChange={e => setNewExpiresAt(e.target.value)}
                    className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100 focus:border-sky-500 focus:outline-none"
                  />
                </div>
                <div className="flex justify-end gap-3">
                  <button onClick={() => setShowModal(false)} className="px-4 py-2 text-sm text-slate-400 hover:text-slate-200">
                    Cancel
                  </button>
                  <button onClick={createKey} disabled={!newName.trim() || submitting}
                    className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-50">
                    {submitting ? "Creating…" : "Create Key"}
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
