import { ThinkingBlock, parseThinking } from "./ThinkingBlock";

interface Message {
  role: "user" | "assistant" | "system";
  content: string;
}

interface Props {
  message: Message;
  streaming?: boolean;
}

export function MessageBubble({ message, streaming }: Props) {
  const isUser = message.role === "user";
  const isSystem = message.role === "system";

  if (isSystem) {
    return (
      <div className="flex justify-center">
        <div className="max-w-2xl rounded-lg border border-slate-700/50 bg-slate-800/40 px-4 py-2 text-xs text-slate-500 italic">
          System: {message.content}
        </div>
      </div>
    );
  }

  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="max-w-2xl rounded-2xl rounded-tr-sm bg-sky-600 px-4 py-3 text-sm text-white shadow-sm">
          {message.content}
        </div>
      </div>
    );
  }

  const { thinking, answer } = parseThinking(message.content);

  return (
    <div className="flex justify-start">
      <div className="max-w-2xl w-full">
        {thinking && <ThinkingBlock content={thinking} />}
        <div className="rounded-2xl rounded-tl-sm bg-slate-800 px-4 py-3 text-sm text-slate-100 shadow-sm">
          <div className="whitespace-pre-wrap">{answer || message.content}</div>
          {streaming && (
            <span className="ml-1 inline-block h-4 w-0.5 animate-pulse bg-sky-400 align-middle" />
          )}
        </div>
      </div>
    </div>
  );
}

export type { Message };
