import { useState, useEffect, useCallback } from "react";
import { Server, Cpu, Zap, ExternalLink } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { makeAdminClient } from "../../api/client";
import { Badge } from "../../components/shared/Badge";
import { ErrorBanner } from "../../components/shared/ErrorBanner";
import type { Worker, Deployment } from "../../api/types";

export function WorkersPage() {
  const auth = useAuth();
  const [workers, setWorkers] = useState<Worker[]>([]);
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const grafanaUrl = import.meta.env.VITE_GRAFANA_URL || "http://localhost:3000";

  const fetchData = useCallback(async () => {
    if (!auth.adminKey) return;
    setLoading(true);
    setError(null);
    try {
      const client = makeAdminClient(auth.gatewayUrl, auth.adminKey);
      const [wRes, dRes] = await Promise.all([
        client.get("/workers"),
        client.get("/deployments"),
      ]);
      setWorkers(wRes.data.workers);
      setDeployments(dRes.data.deployments);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [auth.gatewayUrl, auth.adminKey]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const depByWorker = Object.fromEntries(
    deployments.map(d => [d.worker_id, d])
  );

  return (
    <div className="mx-auto max-w-5xl px-4 py-8">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Workers</h1>
          <p className="mt-0.5 text-sm text-slate-400">GPU worker and deployment health</p>
        </div>
        <div className="flex items-center gap-2">
          <a href={grafanaUrl} target="_blank" rel="noreferrer"
            className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-400 hover:bg-slate-800 hover:text-slate-200">
            <ExternalLink className="h-3.5 w-3.5" /> Grafana
          </a>
          <button onClick={fetchData}
            className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-400 hover:bg-slate-800 hover:text-slate-200">
            Refresh
          </button>
        </div>
      </div>

      {!auth.adminKey && (
        <div className="rounded-lg border border-amber-800/50 bg-amber-950/30 p-4 text-sm text-amber-300 mb-4">
          Set your Admin Key in Settings (⚙️) to view workers.
        </div>
      )}

      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      {loading ? (
        <div className="text-center py-12 text-slate-500 text-sm">Loading…</div>
      ) : (
        <div className="space-y-4">
          {workers.length === 0 ? (
            <div className="rounded-xl border border-slate-800 bg-slate-900 p-8 text-center text-slate-500 text-sm">
              No workers registered
            </div>
          ) : workers.map(w => {
            const dep = depByWorker[w.id];
            const vramGb = (w.gpu_vram_mb / 1024).toFixed(0);
            const cfg = dep?.config_json as Record<string, unknown> | null;
            return (
              <div key={w.id} className="rounded-xl border border-slate-800 bg-slate-900 p-6">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex items-center gap-3">
                    <div className="h-10 w-10 rounded-xl bg-slate-800 flex items-center justify-center">
                      <Server className="h-5 w-5 text-sky-400" />
                    </div>
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-slate-100">{w.hostname}</span>
                        <Badge status={w.status} />
                      </div>
                      <a href={w.endpoint} target="_blank" rel="noreferrer"
                        className="text-xs text-slate-500 hover:text-sky-400 flex items-center gap-1 mt-0.5">
                        {w.endpoint.slice(0, 60)}{w.endpoint.length > 60 ? "…" : ""}
                        <ExternalLink className="h-3 w-3" />
                      </a>
                    </div>
                  </div>
                  <div className="flex items-center gap-4 text-sm text-slate-400">
                    <div className="flex items-center gap-1.5">
                      <Cpu className="h-4 w-4 text-violet-400" />
                      <span>{w.gpu_type}</span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <Zap className="h-4 w-4 text-yellow-400" />
                      <span>{vramGb} GB VRAM</span>
                    </div>
                  </div>
                </div>

                {dep && (
                  <div className="mt-4 rounded-lg border border-slate-700/50 bg-slate-800/40 p-4">
                    <div className="flex items-center gap-2 mb-3">
                      <span className="text-sm font-medium text-slate-300">Active Deployment</span>
                      <Badge status={dep.status} />
                    </div>
                    <div className="grid grid-cols-2 gap-x-8 gap-y-2 text-xs">
                      <div>
                        <span className="text-slate-500">Deployment ID</span>
                        <p className="font-mono text-slate-400 mt-0.5">{dep.id.slice(0, 8)}…</p>
                      </div>
                      {cfg && (
                        <>
                          {cfg.dtype && <div><span className="text-slate-500">dtype</span><p className="text-slate-300 mt-0.5">{String(cfg.dtype)}</p></div>}
                          {cfg.max_model_len && <div><span className="text-slate-500">context</span><p className="text-slate-300 mt-0.5">{String(cfg.max_model_len)} tokens</p></div>}
                          {cfg.enable_lora !== undefined && <div><span className="text-slate-500">LoRA</span><p className="text-slate-300 mt-0.5">{cfg.enable_lora ? "enabled" : "disabled"}</p></div>}
                          {cfg.enable_prefix_caching !== undefined && <div><span className="text-slate-500">Prefix cache</span><p className="text-slate-300 mt-0.5">{cfg.enable_prefix_caching ? "enabled" : "disabled"}</p></div>}
                        </>
                      )}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Grafana iframe */}
      <div className="mt-8 rounded-xl border border-slate-800 overflow-hidden">
        <div className="flex items-center justify-between border-b border-slate-800 bg-slate-900 px-4 py-3">
          <span className="text-sm font-medium text-slate-300">Grafana Dashboard</span>
          <a href={grafanaUrl} target="_blank" rel="noreferrer"
            className="text-xs text-sky-400 hover:text-sky-300 flex items-center gap-1">
            Open in new tab <ExternalLink className="h-3 w-3" />
          </a>
        </div>
        <iframe
          src={`${grafanaUrl}?kiosk=tv`}
          className="w-full"
          style={{ height: 480, border: "none", background: "#0f172a" }}
          title="Grafana Dashboard"
        />
      </div>
    </div>
  );
}
