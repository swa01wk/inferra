import { useState } from "react";
import { ChevronDown, ChevronRight, Brain } from "lucide-react";

export function ThinkingBlock({ content }: { content: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mb-3 rounded-lg border border-violet-800/50 bg-violet-950/30">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex w-full items-center gap-2 px-3 py-2 text-xs text-violet-400 hover:text-violet-300"
      >
        <Brain className="h-3.5 w-3.5" />
        <span className="font-medium">Thinking</span>
        {open ? <ChevronDown className="h-3.5 w-3.5 ml-auto" /> : <ChevronRight className="h-3.5 w-3.5 ml-auto" />}
      </button>
      {open && (
        <div className="border-t border-violet-800/30 px-3 py-2 text-xs text-violet-300/70 whitespace-pre-wrap font-mono">
          {content}
        </div>
      )}
    </div>
  );
}

export function parseThinking(content: string): { thinking: string; answer: string } {
  const match = content.match(/^<think>([\s\S]*?)<\/think>([\s\S]*)$/s);
  if (!match) return { thinking: "", answer: content };
  return { thinking: match[1].trim(), answer: match[2].trim() };
}
