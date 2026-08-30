import { AlertCircle, X } from "lucide-react";

const MESSAGE_MAP: Record<number, string> = {
  401: "Invalid API key — check your keys in Settings (⚙️ top right).",
  403: "This key type cannot be used here (admin key required).",
  400: "Bad request — prompt + max_tokens may exceed the 8,192 token limit.",
  422: "Validation error — check the values you submitted.",
  429: "Rate limited — too many requests. Check Retry-After and try again.",
  503: "Inference unavailable — check the Workers tab.",
};

interface ErrorBannerProps {
  error: string | null;
  onDismiss?: () => void;
}

export function ErrorBanner({ error, onDismiss }: ErrorBannerProps) {
  if (!error) return null;

  const statusMatch = error.match(/(\d{3})/);
  const status = statusMatch ? parseInt(statusMatch[1]) : null;
  const message = status && MESSAGE_MAP[status] ? MESSAGE_MAP[status] : error;

  return (
    <div className="flex items-start gap-3 rounded-lg border border-red-800 bg-red-950/50 p-3 text-sm text-red-300">
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-400" />
      <span className="flex-1">{message}</span>
      {onDismiss && (
        <button onClick={onDismiss} className="text-red-400 hover:text-red-200">
          <X className="h-4 w-4" />
        </button>
      )}
    </div>
  );
}
