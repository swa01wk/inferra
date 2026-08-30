import { useState, useRef, useEffect, useCallback } from "react";
import { Send, RotateCcw, ChevronDown } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { makeOpenAIClient } from "../../api/client";
import { MessageBubble, type Message } from "./MessageBubble";
import { ErrorBanner } from "../../components/shared/ErrorBanner";
import type { Model } from "../../api/types";

interface TokenStats {
  promptTokens: number;
  completionTokens: number;
  ttftMs: number | null;
  totalMs: number | null;
}

export function ChatPage() {
  const auth = useAuth();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("You are a helpful assistant.");
  const [model, setModel] = useState("test-assistant");
  const [models, setModels] = useState<Model[]>([]);
  const [maxTokens, setMaxTokens] = useState(512);
  const [temperature, setTemperature] = useState(0.7);
  const [topP, setTopP] = useState(1.0);
  const [enableThinking, setEnableThinking] = useState(true);
  const [stream, setStream] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stats, setStats] = useState<TokenStats | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const startRef = useRef<number>(0);

  const fetchModels = useCallback(async () => {
    if (!auth.inferenceKey) return;
    try {
      const res = await fetch(`${auth.gatewayUrl}/v1/models`, {
        headers: { Authorization: `Bearer ${auth.inferenceKey}` },
      });
      if (res.ok) {
        const data = await res.json();
        setModels(data.data || []);
        if (data.data?.[0]) setModel(data.data[0].id);
      }
    } catch {}
  }, [auth.gatewayUrl, auth.inferenceKey]);

  useEffect(() => { fetchModels(); }, [fetchModels]);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

  const send = async () => {
    if (!input.trim() || generating) return;
    if (!auth.inferenceKey) { setError("Set your Inference Key in Settings first."); return; }

    const userMsg: Message = { role: "user", content: input.trim() };
    const history: Message[] = systemPrompt
      ? [{ role: "system", content: systemPrompt }, ...messages, userMsg]
      : [...messages, userMsg];

    setMessages(prev => [...prev, userMsg]);
    setInput("");
    setError(null);
    setStats(null);
    setGenerating(true);
    startRef.current = performance.now();

    let ttftMs: number | null = null;
    let assistantContent = "";

    try {
      const client = makeOpenAIClient(auth.gatewayUrl, auth.inferenceKey);

      if (stream) {
        const assistantIdx = messages.length + 1;
        setMessages(prev => [...prev, { role: "assistant", content: "" }]);

        const streamParams = Object.assign(
          { model, messages: history, stream: true as const, max_tokens: maxTokens, temperature, top_p: topP },
          enableThinking ? {} : { enable_thinking: false },
        );
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const completion = await client.chat.completions.create(streamParams as any);

        for await (const chunk of completion) {
          const delta = chunk.choices[0]?.delta?.content ?? "";
          if (delta && ttftMs === null) ttftMs = performance.now() - startRef.current;
          assistantContent += delta;
          setMessages(prev => {
            const next = [...prev];
            next[assistantIdx] = { role: "assistant", content: assistantContent };
            return next;
          });
        }

        setStats({
          promptTokens: 0,
          completionTokens: 0,
          ttftMs,
          totalMs: performance.now() - startRef.current,
        });
      } else {
        const nonStreamParams = Object.assign(
          { model, messages: history, stream: false as const, max_tokens: maxTokens, temperature, top_p: topP },
          enableThinking ? {} : { enable_thinking: false },
        );
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const completion = await client.chat.completions.create(nonStreamParams as any);
        assistantContent = completion.choices[0]?.message?.content ?? "";
        const totalMs = performance.now() - startRef.current;
        setMessages(prev => [...prev, { role: "assistant", content: assistantContent }]);
        setStats({
          promptTokens: completion.usage?.prompt_tokens ?? 0,
          completionTokens: completion.usage?.completion_tokens ?? 0,
          ttftMs: null,
          totalMs,
        });
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      setMessages(prev => prev.filter((_, i) => i < prev.length - (stream ? 1 : 0)));
    } finally {
      setGenerating(false);
    }
  };

  const clear = () => { setMessages([]); setStats(null); setError(null); };

  return (
    <div className="flex h-[calc(100vh-3.5rem)]">
      {/* Settings sidebar */}
      <aside className="w-64 shrink-0 border-r border-slate-800 bg-slate-900/50 p-4 overflow-y-auto hidden md:block">
        <h3 className="mb-4 text-xs font-semibold uppercase tracking-wider text-slate-500">Settings</h3>

        <div className="space-y-4">
          <div>
            <label className="mb-1 block text-xs text-slate-400">Model</label>
            {models.length > 0 ? (
              <div className="relative">
                <select
                  value={model}
                  onChange={e => setModel(e.target.value)}
                  className="w-full appearance-none rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 pr-8 text-sm text-slate-100 focus:border-sky-500 focus:outline-none"
                >
                  {models.map(m => <option key={m.id} value={m.id}>{m.id}</option>)}
                </select>
                <ChevronDown className="pointer-events-none absolute right-2 top-2.5 h-4 w-4 text-slate-500" />
              </div>
            ) : (
              <input
                value={model}
                onChange={e => setModel(e.target.value)}
                className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100 focus:border-sky-500 focus:outline-none"
                placeholder="test-assistant"
              />
            )}
          </div>

          <div>
            <label className="mb-1 block text-xs text-slate-400">System prompt</label>
            <textarea
              value={systemPrompt}
              onChange={e => setSystemPrompt(e.target.value)}
              rows={3}
              className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:border-sky-500 focus:outline-none resize-none"
            />
          </div>

          <div>
            <label className="mb-1 flex justify-between text-xs text-slate-400">
              <span>Max tokens</span><span className="text-slate-300">{maxTokens}</span>
            </label>
            <input type="range" min={64} max={2048} step={64} value={maxTokens}
              onChange={e => setMaxTokens(+e.target.value)}
              className="w-full accent-sky-500" />
          </div>

          <div>
            <label className="mb-1 flex justify-between text-xs text-slate-400">
              <span>Temperature</span><span className="text-slate-300">{temperature.toFixed(1)}</span>
            </label>
            <input type="range" min={0} max={1.5} step={0.1} value={temperature}
              onChange={e => setTemperature(+e.target.value)}
              className="w-full accent-sky-500" />
          </div>

          <div>
            <label className="mb-1 flex justify-between text-xs text-slate-400">
              <span>Top P</span><span className="text-slate-300">{topP.toFixed(2)}</span>
            </label>
            <input type="range" min={0.01} max={1} step={0.01} value={topP}
              onChange={e => setTopP(+e.target.value)}
              className="w-full accent-sky-500" />
          </div>

          <div className="flex items-center gap-2">
            <input type="checkbox" id="stream" checked={stream}
              onChange={e => setStream(e.target.checked)}
              className="h-4 w-4 accent-sky-500" />
            <label htmlFor="stream" className="text-xs text-slate-400">Streaming</label>
          </div>

          <div className="flex items-center gap-2">
            <input type="checkbox" id="thinking" checked={enableThinking}
              onChange={e => setEnableThinking(e.target.checked)}
              className="h-4 w-4 accent-violet-500" />
            <label htmlFor="thinking" className="text-xs text-slate-400">
              Thinking <span className="text-violet-400/70">(Qwen3)</span>
            </label>
          </div>

          <button onClick={clear}
            className="flex w-full items-center justify-center gap-2 rounded-lg border border-slate-700 py-2 text-xs text-slate-400 hover:bg-slate-800 hover:text-slate-200">
            <RotateCcw className="h-3.5 w-3.5" /> Clear chat
          </button>
        </div>
      </aside>

      {/* Chat area */}
      <div className="flex flex-1 flex-col min-w-0">
        <div className="flex-1 overflow-y-auto px-4 py-6">
          {messages.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center text-center text-slate-500">
              <div className="mb-3 h-12 w-12 rounded-xl bg-sky-600/20 flex items-center justify-center">
                <span className="text-sky-400 font-bold text-xl">I</span>
              </div>
              <p className="text-sm font-medium text-slate-400">Inferra Chat Playground</p>
              <p className="mt-1 text-xs">Connected to {model} · {auth.gatewayUrl}</p>
            </div>
          ) : (
            <div className="mx-auto max-w-3xl space-y-4">
              {messages.map((m, i) => (
                <MessageBubble key={i} message={m} streaming={generating && i === messages.length - 1} />
              ))}
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {/* Error */}
        {error && (
          <div className="px-4 pb-2 max-w-3xl mx-auto w-full">
            <ErrorBanner error={error} onDismiss={() => setError(null)} />
          </div>
        )}

        {/* Token stats bar */}
        {stats && (
          <div className="border-t border-slate-800 px-4 py-2 flex items-center gap-4 text-xs text-slate-500">
            {stats.promptTokens > 0 && <span>Prompt: <span className="text-slate-300">{stats.promptTokens}</span> tok</span>}
            {stats.completionTokens > 0 && <span>Completion: <span className="text-slate-300">{stats.completionTokens}</span> tok</span>}
            {stats.ttftMs != null && <span>TTFT: <span className="text-sky-400">{stats.ttftMs.toFixed(0)} ms</span></span>}
            {stats.totalMs != null && <span>Total: <span className="text-slate-300">{stats.totalMs.toFixed(0)} ms</span></span>}
          </div>
        )}

        {/* Input bar */}
        <div className="border-t border-slate-800 bg-slate-900/50 p-4">
          <div className="mx-auto flex max-w-3xl gap-3">
            <textarea
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
              placeholder="Type a message… (Enter to send, Shift+Enter for newline)"
              rows={2}
              disabled={generating}
              className="flex-1 rounded-xl border border-slate-700 bg-slate-800 px-4 py-3 text-sm text-slate-100 placeholder-slate-500 focus:border-sky-500 focus:outline-none resize-none disabled:opacity-50"
            />
            <button
              onClick={send}
              disabled={generating || !input.trim()}
              className="flex h-12 w-12 shrink-0 items-center justify-center self-end rounded-xl bg-sky-600 text-white transition hover:bg-sky-500 disabled:opacity-40"
            >
              {generating ? (
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
              ) : (
                <Send className="h-4 w-4" />
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
