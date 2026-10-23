import React from "react";
import clsx from "clsx";
import { SourceName } from "../../types/api";

interface StatusBadgeProps {
  status?: "healthy" | "degraded" | "unavailable" | "live" | "stale" | "positive" | "negative" | "warning" | "disabled";
  label?: string;
  className?: string;
}

export const StatusBadge: React.FC<StatusBadgeProps> = ({ status = "healthy", label, className }) => {
  const configs = {
    healthy: {
      bg: "bg-emerald-500/10 text-emerald-400 border-emerald-500/30",
      dot: "bg-emerald-400",
      text: label || "Aktif",
    },
    disabled: {
      bg: "bg-slate-500/10 text-slate-400 border-slate-500/30",
      dot: "bg-slate-400",
      text: label || "Devre Dışı",
    },
    live: {
      bg: "bg-emerald-500/10 text-emerald-400 border-emerald-500/30",
      dot: "bg-emerald-400 animate-pulse",
      text: label || "Canlı",
    },
    positive: {
      bg: "bg-emerald-500/10 text-emerald-400 border-emerald-500/30",
      dot: "bg-emerald-400",
      text: label || "Pozitif",
    },
    degraded: {
      bg: "bg-amber-500/10 text-amber-400 border-amber-500/30",
      dot: "bg-amber-400",
      text: label || "Kısmi Sorun",
    },
    stale: {
      bg: "bg-amber-500/10 text-amber-400 border-amber-500/30",
      dot: "bg-amber-400",
      text: label || "Bayat",
    },
    warning: {
      bg: "bg-amber-500/10 text-amber-400 border-amber-500/30",
      dot: "bg-amber-400",
      text: label || "Uyarı",
    },
    unavailable: {
      bg: "bg-rose-500/10 text-rose-400 border-rose-500/30",
      dot: "bg-rose-400",
      text: label || "Ulaşılamıyor",
    },
    negative: {
      bg: "bg-rose-500/10 text-rose-400 border-rose-500/30",
      dot: "bg-rose-400",
      text: label || "Negatif",
    },
  };

  const config = configs[status] || configs.healthy;

  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium border",
        config.bg,
        className
      )}
    >
      <span className={clsx("w-1.5 h-1.5 rounded-full", config.dot)} />
      <span>{config.text}</span>
    </span>
  );
};

export const SourceBadge: React.FC<{ source: SourceName; className?: string }> = ({
  source,
  className,
}) => {
  const configs: Record<SourceName, { name: string; bg: string; text: string; border: string }> = {
    source_c: {
      name: "Kaynak C",
      bg: "bg-purple-950/60",
      text: "text-purple-300",
      border: "border-purple-800/50",
    },
    source_b: {
      name: "Kaynak B",
      bg: "bg-blue-950/60",
      text: "text-blue-300",
      border: "border-blue-800/50",
    },
    source_a: {
      name: "Kaynak A",
      bg: "bg-amber-950/60",
      text: "text-amber-300",
      border: "border-amber-800/50",
    },
    source_d: {
      name: "Kaynak D",
      bg: "bg-emerald-950/60",
      text: "text-emerald-300",
      border: "border-emerald-800/50",
    },
    source_e: {
      name: "Kaynak E",
      bg: "bg-rose-950/60",
      text: "text-rose-300",
      border: "border-rose-800/50",
    },
    source_f: {
      name: "Kaynak F",
      bg: "bg-teal-950/60",
      text: "text-teal-300",
      border: "border-teal-800/50",
    },
  };

  const config = configs[source] || {
    name: source,
    bg: "bg-slate-800",
    text: "text-slate-300",
    border: "border-slate-700",
  };

  return (
    <span
      className={clsx(
        "px-2 py-0.5 rounded text-[11px] font-semibold tracking-wide uppercase border",
        config.bg,
        config.text,
        config.border,
        className
      )}
    >
      {config.name}
    </span>
  );
};
