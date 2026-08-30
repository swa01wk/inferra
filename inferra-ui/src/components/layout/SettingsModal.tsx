import { useState } from "react";
import { X, Settings } from "lucide-react";
import { useAuth } from "../../context/AuthContext";

export function SettingsModal() {
  const auth = useAuth();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({
    gatewayUrl: auth.gatewayUrl,
    inferenceKey: auth.inferenceKey,
    adminKey: auth.adminKey,
  });

  const save = () => {
    auth.save(form);
    setOpen(false);
  };

  return (
    <>
      <button
        onClick={() => { setForm({ gatewayUrl: auth.gatewayUrl, inferenceKey: auth.inferenceKey, adminKey: auth.adminKey }); setOpen(true); }}
        className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm text-slate-400 hover:bg-slate-800 hover:text-slate-200 transition-colors"
        title="Settings"
      >
        <Settings className="h-4 w-4" />
        <span className="hidden sm:inline">Settings</span>
      </button>

      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-xl border border-slate-700 bg-slate-900 p-6 shadow-2xl">
            <div className="mb-5 flex items-center justify-between">
              <h2 className="text-lg font-semibold text-slate-100">Settings</h2>
              <button onClick={() => setOpen(false)} className="text-slate-500 hover:text-slate-300">
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="space-y-4">
              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-400">Gateway URL</label>
                <input
                  className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:border-sky-500 focus:outline-none"
                  value={form.gatewayUrl}
                  onChange={e => setForm(f => ({ ...f, gatewayUrl: e.target.value }))}
                  placeholder="http://localhost:9100"
                />
              </div>
              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-400">Inference Key</label>
                <input
                  type="password"
                  className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:border-sky-500 focus:outline-none"
                  value={form.inferenceKey}
                  onChange={e => setForm(f => ({ ...f, inferenceKey: e.target.value }))}
                  placeholder="inf_..."
                />
                <p className="mt-1 text-xs text-slate-500">Used for Chat, Adapters, Usage</p>
              </div>
              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-400">Admin Key</label>
                <input
                  type="password"
                  className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:border-sky-500 focus:outline-none"
                  value={form.adminKey}
                  onChange={e => setForm(f => ({ ...f, adminKey: e.target.value }))}
                  placeholder="inf_..."
                />
                <p className="mt-1 text-xs text-slate-500">Used for API Keys, Workers</p>
              </div>
            </div>

            <div className="mt-6 flex justify-end gap-3">
              <button
                onClick={() => setOpen(false)}
                className="rounded-lg px-4 py-2 text-sm text-slate-400 hover:text-slate-200"
              >
                Cancel
              </button>
              <button
                onClick={save}
                className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500"
              >
                Save
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
