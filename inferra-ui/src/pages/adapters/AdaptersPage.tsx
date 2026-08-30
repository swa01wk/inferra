import { useState, useEffect, useCallback, useRef } from "react";
import { Plus, RefreshCw, Trash2, AlertCircle } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { makeInferenceClient } from "../../api/client";
import { Badge } from "../../components/shared/Badge";
import { ErrorBanner } from "../../components/shared/ErrorBanner";
import type { Adapter } from "../../api/types";

const IN_PROGRESS = new Set(["registered", "downloading", "available"]);

export function AdaptersPage() {
  const auth = useAuth();
  const [adapters, setAdapters] = useState<Adapter[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showModal, setShowModal] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [form, setForm] = useState({ name: "", storage_uri: "", rank: 16, alias: "" });

  const fetchAdapters = useCallback(async () => {
    if (!auth.inferenceKey) return;
    try {
      const client = makeInferenceClient(auth.gatewayUrl, auth.inferenceKey);
      const res = await client.get("/adapters");
      setAdapters(res.data.adapters ?? []);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [auth.gatewayUrl, auth.inferenceKey]);

  useEffect(() => {
    setLoading(true);
    fetchAdapters();
  }, [fetchAdapters]);

  useEffect(() => {
    const hasInProgress = adapters.some(a => IN_PROGRESS.has(a.status));
    if (hasInProgress && !pollRef.current) {
      pollRef.current = setInterval(fetchAdapters, 3000);
    } else if (!hasInProgress && pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [adapters, fetchAdapters]);

  const register = async () => {
    if (!form.name.trim() || !form.storage_uri.trim() || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const client = makeInferenceClient(auth.gatewayUrl, auth.inferenceKey);
      await client.post("/adapters", {
        name: form.name.trim(),
        storage_uri: form.storage_uri.trim(),
        base_model: "Qwen/Qwen3-4B",
        rank: form.rank,
        alias: form.alias.trim() || undefined,
      });
      setShowModal(false);
      setForm({ name: "", storage_uri: "", rank: 16, alias: "" });
      await fetchAdapters();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  const deleteAdapter = async (id: string) => {
    if (!confirm("Delete this adapter?")) return;
    try {
      const client = makeInferenceClient(auth.gatewayUrl, auth.inferenceKey);
      await client.delete(`/adapters/${id}`);
      await fetchAdapters();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const inProgressCount = adapters.filter(a => IN_PROGRESS.has(a.status)).length;

  return (
    <div className="mx-auto max-w-5xl px-4 py-8">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">LoRA Adapters</h1>
          <p className="mt-0.5 text-sm text-slate-400">Register and manage fine-tuned adapters</p>
        </div>
        <div className="flex items-center gap-2">
          {inProgressCount > 0 && (
            <span className="flex items-center gap-1.5 text-xs text-yellow-400 animate-pulse">
              <RefreshCw className="h-3 w-3 animate-spin" />
              {inProgressCount} loading…
            </span>
          )}
          <button onClick={() => setShowModal(true)}
            className="flex items-center gap-2 rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500">
            <Plus className="h-4 w-4" /> Register
          </button>
        </div>
      </div>

      {!auth.inferenceKey && (
        <div className="rounded-lg border border-amber-800/50 bg-amber-950/30 p-4 text-sm text-amber-300 mb-4">
          Set your Inference Key in Settings (⚙️) to manage adapters.
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
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Rank</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Status</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Storage URI</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Error</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400"></th>
              </tr>
            </thead>
            <tbody>
              {adapters.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-4 py-8 text-center text-slate-500">No adapters registered</td>
                </tr>
              ) : adapters.map(a => (
                <tr key={a.id} className="border-b border-slate-800/50 last:border-0 hover:bg-slate-800/30">
                  <td className="px-4 py-3 font-medium text-slate-200">{a.name}</td>
                  <td className="px-4 py-3 text-slate-400">{a.rank}</td>
                  <td className="px-4 py-3"><Badge status={a.status} /></td>
                  <td className="px-4 py-3 font-mono text-xs text-slate-500 max-w-xs truncate">{a.storage_uri}</td>
                  <td className="px-4 py-3">
                    {a.error_message && (
                      <span className="flex items-center gap-1 text-xs text-red-400">
                        <AlertCircle className="h-3 w-3" />{a.error_message.slice(0, 60)}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <button onClick={() => deleteAdapter(a.id)} className="text-slate-500 hover:text-red-400 p-1">
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Register modal */}
      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-xl border border-slate-700 bg-slate-900 p-6 shadow-2xl">
            <h2 className="mb-5 text-lg font-semibold text-slate-100">Register Adapter</h2>
            <div className="space-y-4">
              {[
                { label: "Name", key: "name", placeholder: "e.g. finance-v1" },
                { label: "Storage URI", key: "storage_uri", placeholder: "s3://inferra-adapters/my-adapter.bin" },
                { label: "Alias (optional)", key: "alias", placeholder: "e.g. finance-bot" },
              ].map(({ label, key, placeholder }) => (
                <div key={key}>
                  <label className="mb-1.5 block text-xs font-medium text-slate-400">{label}</label>
                  <input value={form[key as keyof typeof form] as string}
                    onChange={e => setForm(f => ({ ...f, [key]: e.target.value }))}
                    placeholder={placeholder}
                    className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:border-sky-500 focus:outline-none" />
                </div>
              ))}
              <div>
                <label className="mb-1.5 flex justify-between text-xs font-medium text-slate-400">
                  <span>Rank</span><span>{form.rank}</span>
                </label>
                <input type="range" min={1} max={16} value={form.rank}
                  onChange={e => setForm(f => ({ ...f, rank: +e.target.value }))}
                  className="w-full accent-sky-500" />
              </div>
            </div>
            <div className="mt-6 flex justify-end gap-3">
              <button onClick={() => setShowModal(false)} className="px-4 py-2 text-sm text-slate-400 hover:text-slate-200">Cancel</button>
              <button onClick={register} disabled={!form.name.trim() || !form.storage_uri.trim() || submitting}
                className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-50">
                {submitting ? "Registering…" : "Register"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
