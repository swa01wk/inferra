import { useState, useEffect, useCallback } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
} from "recharts";
import { useAuth } from "../../context/AuthContext";
import { makeInferenceClient } from "../../api/client";
import { Badge } from "../../components/shared/Badge";
import { ErrorBanner } from "../../components/shared/ErrorBanner";
import type { UsageRequest, UsageSummary } from "../../api/types";

function LatencyBar({ label, value, max, color }: { label: string; value: number; max: number; color: string }) {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0;
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-24 shrink-0 text-slate-400">{label}</span>
      <div className="flex-1 h-3 rounded-full bg-slate-800">
        <div className="h-3 rounded-full transition-all" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="w-14 text-right text-slate-300">{value.toFixed(0)} ms</span>
    </div>
  );
}

function ExpandedRow({ req }: { req: UsageRequest }) {
  const total = req.total_ms ?? 0;
  const ttft = req.ttft_ms ?? 0;
  const decode = total - ttft;
  return (
    <tr>
      <td colSpan={7} className="bg-slate-800/30 px-8 pb-4 pt-2">
        <div className="space-y-2">
          {ttft > 0 && <LatencyBar label="TTFT" value={ttft} max={total} color="#38bdf8" />}
          {decode > 0 && <LatencyBar label="Decode" value={decode} max={total} color="#818cf8" />}
          {total > 0 && <LatencyBar label="Total" value={total} max={total} color="#94a3b8" />}
          <div className="flex gap-4 pt-1 text-xs text-slate-500">
            <span>Prompt: <span className="text-slate-300">{req.prompt_tokens}</span> tok</span>
            <span>Completion: <span className="text-slate-300">{req.completion_tokens}</span> tok</span>
            {ttft > 0 && total > 0 && (
              <span>Tok/s: <span className="text-slate-300">
                {((req.completion_tokens / ((total - ttft) / 1000)) || 0).toFixed(1)}
              </span></span>
            )}
          </div>
        </div>
      </td>
    </tr>
  );
}

export function UsagePage() {
  const auth = useAuth();
  const [data, setData] = useState<UsageSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  const fetchUsage = useCallback(async () => {
    if (!auth.inferenceKey) return;
    setLoading(true);
    setError(null);
    try {
      const client = makeInferenceClient(auth.gatewayUrl, auth.inferenceKey);
      const res = await client.get("/usage");
      setData(res.data);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [auth.gatewayUrl, auth.inferenceKey]);

  useEffect(() => { fetchUsage(); }, [fetchUsage]);

  const chartData = (data?.requests ?? [])
    .filter(r => r.ttft_ms != null)
    .slice(0, 20)
    .reverse()
    .map((r, i) => ({ i, ttft: r.ttft_ms ?? 0, total: r.total_ms ?? 0 }));

  return (
    <div className="mx-auto max-w-6xl px-4 py-8">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Usage</h1>
          <p className="mt-0.5 text-sm text-slate-400">Per-request history with latency breakdown</p>
        </div>
        <button onClick={fetchUsage}
          className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-400 hover:bg-slate-800 hover:text-slate-200">
          Refresh
        </button>
      </div>

      {!auth.inferenceKey && (
        <div className="rounded-lg border border-amber-800/50 bg-amber-950/30 p-4 text-sm text-amber-300 mb-4">
          Set your Inference Key in Settings (⚙️) to view usage.
        </div>
      )}

      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      {data && (
        <>
          {/* Summary cards */}
          <div className="mb-6 grid grid-cols-2 gap-4 sm:grid-cols-4">
            {[
              { label: "Total Requests", value: data.total_requests },
              { label: "Prompt Tokens", value: data.total_prompt_tokens.toLocaleString() },
              { label: "Completion Tokens", value: data.total_completion_tokens.toLocaleString() },
              { label: "Avg TTFT",
                value: (() => {
                  const ttfts = data.requests.filter(r => r.ttft_ms != null).map(r => r.ttft_ms!);
                  return ttfts.length ? `${(ttfts.reduce((a, b) => a + b, 0) / ttfts.length).toFixed(0)} ms` : "—";
                })()
              },
            ].map(({ label, value }) => (
              <div key={label} className="rounded-xl border border-slate-800 bg-slate-900 p-4">
                <div className="text-xs text-slate-400">{label}</div>
                <div className="mt-1 text-2xl font-semibold text-sky-400">{value}</div>
              </div>
            ))}
          </div>

          {/* Chart */}
          {chartData.length > 2 && (
            <div className="mb-6 rounded-xl border border-slate-800 bg-slate-900 p-4">
              <h2 className="mb-4 text-sm font-medium text-slate-300">Recent Latency (last 20 requests)</h2>
              <ResponsiveContainer width="100%" height={160}>
                <BarChart data={chartData} barSize={8}>
                  <XAxis dataKey="i" hide />
                  <YAxis tick={{ fontSize: 10, fill: "#64748b" }} width={40} />
                  <Tooltip
                    contentStyle={{ background: "#1e293b", border: "1px solid #334155", borderRadius: 8, fontSize: 12 }}
                    formatter={(v, name) => [`${Number(v).toFixed(0)} ms`, name === "ttft" ? "TTFT" : "Total"]}
                  />
                  <Bar dataKey="ttft" fill="#38bdf8" radius={[2, 2, 0, 0]} />
                  <Bar dataKey="total" fill="#334155" radius={[2, 2, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </>
      )}

      {loading ? (
        <div className="text-center py-12 text-slate-500 text-sm">Loading…</div>
      ) : (
        <div className="rounded-xl border border-slate-800 bg-slate-900 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-800 text-left">
                <th className="w-6 px-4 py-3"></th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Request</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Model</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Status</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">TTFT</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Total</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Tokens</th>
                <th className="px-4 py-3 text-xs font-medium text-slate-400">Time</th>
              </tr>
            </thead>
            <tbody>
              {(data?.requests ?? []).length === 0 ? (
                <tr>
                  <td colSpan={8} className="px-4 py-8 text-center text-slate-500">No requests yet</td>
                </tr>
              ) : (data?.requests ?? []).map(req => (
                <>
                  <tr key={req.request_id}
                    className="border-b border-slate-800/50 hover:bg-slate-800/30 cursor-pointer"
                    onClick={() => setExpanded(e => e === req.request_id ? null : req.request_id)}>
                    <td className="px-4 py-3 text-slate-500">
                      {expanded === req.request_id
                        ? <ChevronDown className="h-3.5 w-3.5" />
                        : <ChevronRight className="h-3.5 w-3.5" />}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-slate-400">{req.request_id.slice(0, 8)}…</td>
                    <td className="px-4 py-3 text-slate-300">{req.logical_model}</td>
                    <td className="px-4 py-3"><Badge status={req.status} /></td>
                    <td className="px-4 py-3 text-slate-400 text-xs">
                      {req.ttft_ms != null ? `${req.ttft_ms.toFixed(0)} ms` : "—"}
                    </td>
                    <td className="px-4 py-3 text-slate-400 text-xs">
                      {req.total_ms != null ? `${req.total_ms.toFixed(0)} ms` : "—"}
                    </td>
                    <td className="px-4 py-3 text-slate-400 text-xs">
                      {req.prompt_tokens + req.completion_tokens > 0
                        ? `${req.prompt_tokens}+${req.completion_tokens}`
                        : "—"}
                    </td>
                    <td className="px-4 py-3 text-slate-500 text-xs">
                      {req.received_at ? new Date(req.received_at).toLocaleTimeString() : "—"}
                    </td>
                  </tr>
                  {expanded === req.request_id && <ExpandedRow key={`${req.request_id}-exp`} req={req} />}
                </>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
