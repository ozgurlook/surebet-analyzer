import React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { Server, RefreshCw, CheckCircle2, AlertTriangle, XCircle, Clock, Database } from "lucide-react";
import { StatusBadge, SourceBadge } from "../components/common/StatusBadge";
import { SourceName } from "../types/api";
import clsx from "clsx";

export const SourcesHealthPage: React.FC = () => {
  const queryClient = useQueryClient();

  const { data: sources, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["sources"],
    queryFn: api.getSourcesHealth,
    refetchInterval: 10000,
  });
  const { data: settings } = useQuery({
    queryKey: ["settings"],
    queryFn: api.getSettings,
  });

  const refreshMutation = useMutation({
    mutationFn: api.refreshSources,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["opportunities"] });
      queryClient.invalidateQueries({ queryKey: ["events"] });
    },
  });

  const sourceMeta: Record<SourceName, { name: string; endpoint: string; proto: string }> = {
    source_c: {
      name: "Kaynak C",
      endpoint: "WAMP v2 feed",
      proto: "WAMP v2 Anonymous WebSocket",
    },
    source_b: {
      name: "Kaynak B",
      endpoint: "WAMP v2 feed",
      proto: "WAMP v2 Anonymous WebSocket",
    },
    source_a: {
      name: "Kaynak A",
      endpoint: "WebSocket feed",
      proto: "JSON-RPC over WebSocket",
    },
    source_d: {
      name: "Kaynak D",
      endpoint: "HTTPS feed",
      proto: "Anonymous JSON HTTP API",
    },
    source_e: {
      name: "Kaynak E",
      endpoint: "WAMP v2 feed",
      proto: "WAMP v2 Anonymous WebSocket",
    },
    source_f: {
      name: "Kaynak F",
      endpoint: "WAMP v2 feed",
      proto: "WAMP v2 Anonymous WebSocket",
    },
  };

  const sourcesList: SourceName[] = [
    "source_c",
    "source_b",
    "source_a",
    "source_d",
    "source_e",
    "source_f",
  ];

  return (
    <div className="space-y-3 pb-8">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-bold text-white">Veri Kaynakları Sağlığı</h2>
          <p className="text-[11px] text-slate-400">
            {settings
              ? `${settings.system.refresh_interval_seconds} saniyede bir eşzamanlı anonim toplama döngüsü`
              : "Ayarlanan aralıkla eşzamanlı anonim toplama döngüsü"}
          </p>
        </div>

        <button
          onClick={() => refreshMutation.mutate()}
          disabled={refreshMutation.isPending}
          className="touch-target px-3 py-1.5 rounded-xl bg-brand-primary/20 hover:bg-brand-primary/30 border border-brand-primary/40 text-xs font-semibold text-brand-primary flex items-center gap-1.5 transition active:scale-95 disabled:opacity-50"
        >
          <RefreshCw
            className={`w-3.5 h-3.5 ${refreshMutation.isPending ? "animate-spin" : ""}`}
          />
          <span>Şimdi Yenile</span>
        </button>
      </div>

      {refreshMutation.isError && (
        <div role="alert" className="p-2.5 rounded-xl bg-rose-500/10 border border-rose-500/30 text-xs text-rose-300">
          {(refreshMutation.error as Error).message || "Yenileme isteği gönderilemedi."}
        </div>
      )}

      {isError && sources !== undefined && (
        <div role="alert" className="rounded-xl border border-amber-800/50 bg-amber-950/30 px-3 py-2 text-[11px] text-amber-300">
          Kaynak sağlığı güncellenemedi; son bilinen değerler gösteriliyor.
        </div>
      )}

      {isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              className="bg-navy-900/60 rounded-2xl border border-navy-800 p-4 animate-pulse h-32"
            />
          ))}
        </div>
      ) : isError && sources === undefined ? (
        <div role="alert" className="rounded-2xl border border-rose-500/30 bg-rose-500/10 p-5 text-center text-xs text-rose-300">
          <p className="font-semibold">Kaynak sağlığı yüklenemedi.</p>
          {error instanceof Error && <p className="mt-1">{error.message}</p>}
          <button type="button" onClick={() => refetch()} className="touch-target mt-3 rounded-xl border border-rose-500/30 px-3 font-semibold">
            Tekrar Dene
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          {sourcesList.map((src) => {
            const h = sources?.[src];
            const meta = sourceMeta[src];
            const isHealthy = h?.status === "healthy";
            const isDegraded = h?.status === "degraded";

            const lastSuccessStr = h?.last_success_at
              ? new Date(h.last_success_at).toLocaleTimeString("tr-TR")
              : "Henüz yok";

            return (
              <div
                key={src}
                className="bg-navy-900 rounded-2xl border border-navy-800 p-4 shadow-md space-y-3"
              >
                {/* Header */}
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <SourceBadge source={src} />
                    <span className="text-xs font-medium text-slate-300">
                      {meta.endpoint}
                    </span>
                  </div>
                  <StatusBadge status={h?.status || "unavailable"} />
                </div>

                {/* Metrics Grid */}
                <div className="grid grid-cols-3 gap-2 bg-navy-950/80 rounded-xl p-2.5 border border-navy-850 text-center font-mono">
                  <div>
                    <span className="text-[10px] text-slate-400 block mb-0.5 font-sans">
                      Toplanan Maç
                    </span>
                    <span className="text-sm font-bold text-white">
                      {h?.event_count ?? 0}
                    </span>
                  </div>

                  <div className="border-x border-navy-850">
                    <span className="text-[10px] text-slate-400 block mb-0.5 font-sans">
                      Toplama Süresi
                    </span>
                    <span className="text-sm font-bold text-brand-primary">
                      {h?.duration_ms ?? 0}ms
                    </span>
                  </div>

                  <div>
                    <span className="text-[10px] text-slate-400 block mb-0.5 font-sans">
                      Son Başarı
                    </span>
                    <span className="text-xs font-semibold text-slate-300">
                      {lastSuccessStr}
                    </span>
                  </div>
                </div>

                {/* Status / Error Banner */}
                {h?.error_message && (
                  <div
                    className={clsx(
                      "text-[11px] rounded-lg p-2 border font-mono",
                      h?.status === "disabled"
                        ? "bg-slate-800/40 text-slate-400 border-slate-700/50"
                        : "bg-rose-500/10 text-rose-300 border-rose-500/20"
                    )}
                  >
                    {h?.status === "disabled" ? h.error_message : `Hata: ${h.error_message}`}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
