import React from "react";
import { RefreshCw, Activity } from "lucide-react";
import { StatusBadge } from "../common/StatusBadge";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";

interface TopHeaderProps {
  title?: string;
  status?: "live" | "degraded" | "stale" | "unavailable";
  lastRefreshAt?: string | null;
}

export const TopHeader: React.FC<TopHeaderProps> = ({
  title = "Surebet Analyzer",
  status = "live",
  lastRefreshAt,
}) => {
  const queryClient = useQueryClient();

  const refreshMutation = useMutation({
    mutationFn: api.refreshSources,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["opportunities"] });
      queryClient.invalidateQueries({ queryKey: ["sources"] });
      queryClient.invalidateQueries({ queryKey: ["events"] });
    },
  });

  return (
    <header className="sticky top-0 z-40 bg-navy-950/90 backdrop-blur-md border-b border-navy-800/80 px-4 py-3 flex items-center justify-between">
      <div className="flex items-center gap-2.5">
        <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-brand-primary/20 to-blue-600/30 border border-brand-primary/40 flex items-center justify-center shadow-inner">
          <Activity className="w-4 h-4 text-brand-primary" />
        </div>
        <div>
          <h1 className="text-sm font-bold tracking-tight text-white flex items-center gap-1.5">
            {title}
          </h1>
          <div className="flex items-center gap-1.5 text-[10px] text-slate-400">
            <span>Pre-match 1X2</span>
            <span>•</span>
            <StatusBadge status={status} className="py-0 px-1.5 text-[10px]" />
            {lastRefreshAt && (
              <span className="font-mono text-[9px] text-slate-500">
                {new Date(lastRefreshAt).toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit" })}
              </span>
            )}
          </div>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <button
          onClick={() => refreshMutation.mutate()}
          disabled={refreshMutation.isPending}
          className="touch-target p-2 rounded-lg bg-navy-850 hover:bg-navy-800 active:scale-95 border border-navy-700 text-slate-300 hover:text-white transition flex items-center justify-center"
          title="Verileri Yenile"
          aria-label="Verileri Yenile"
        >
          <RefreshCw
            className={`w-4 h-4 text-brand-primary ${
              refreshMutation.isPending ? "animate-spin" : ""
            }`}
          />
        </button>
        {refreshMutation.isError && (
          <span
            role="alert"
            className="absolute right-4 top-full mt-1 max-w-[260px] rounded-lg border border-rose-500/30 bg-navy-950 px-2 py-1 text-[10px] text-rose-300 shadow-lg"
          >
            {refreshMutation.error instanceof Error
              ? refreshMutation.error.message
              : "Veriler yenilenemedi."}
          </span>
        )}
      </div>
    </header>
  );
};
