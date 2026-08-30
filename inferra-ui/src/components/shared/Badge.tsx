interface BadgeProps {
  status: string;
  className?: string;
}

const colorMap: Record<string, string> = {
  active: "bg-green-900/60 text-green-300 border-green-700",
  healthy: "bg-green-900/60 text-green-300 border-green-700",
  running: "bg-green-900/60 text-green-300 border-green-700",
  loaded: "bg-green-900/60 text-green-300 border-green-700",
  completed: "bg-green-900/60 text-green-300 border-green-700",
  revoked: "bg-red-900/60 text-red-300 border-red-700",
  failed: "bg-red-900/60 text-red-300 border-red-700",
  offline: "bg-red-900/60 text-red-300 border-red-700",
  suspended: "bg-red-900/60 text-red-300 border-red-700",
  downloading: "bg-blue-900/60 text-blue-300 border-blue-700",
  available: "bg-yellow-900/60 text-yellow-300 border-yellow-700",
  registered: "bg-slate-700/60 text-slate-300 border-slate-600",
  pending: "bg-yellow-900/60 text-yellow-300 border-yellow-700",
  cancelled: "bg-slate-700/60 text-slate-300 border-slate-600",
};

export function Badge({ status, className = "" }: BadgeProps) {
  const color = colorMap[status] ?? "bg-slate-700/60 text-slate-300 border-slate-600";
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border ${color} ${className}`}>
      {status}
    </span>
  );
}
