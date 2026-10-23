import React from "react";
import { SearchX, ArrowRight, RefreshCw } from "lucide-react";

interface EmptyStateProps {
  onGoToAllEvents?: () => void;
  onRefresh?: () => void;
  refreshIntervalSeconds?: number;
  refreshing?: boolean;
  refreshError?: string | null;
}

export const EmptyState: React.FC<EmptyStateProps> = ({
  onGoToAllEvents,
  onRefresh,
  refreshIntervalSeconds,
  refreshing = false,
  refreshError,
}) => {
  return (
    <div className="bg-navy-900/60 border border-navy-800/80 rounded-2xl p-6 text-center my-6 flex flex-col items-center">
      <div className="w-12 h-12 rounded-full bg-navy-800/80 border border-navy-700 flex items-center justify-center mb-3 text-slate-400">
        <SearchX className="w-6 h-6" />
      </div>
      <h3 className="text-base font-bold text-white mb-1">
        Şu Anda Pozitif Fırsat Yok
      </h3>
      <p className="text-xs text-slate-400 max-w-xs mb-4">
        Mevcut bütçe ve filtreleriniz altında net farkı pozitif hesaplanan fırsat
        bulunamadı. Oranlar
        {refreshIntervalSeconds ? ` ${refreshIntervalSeconds} saniyede bir` : " düzenli olarak"}
        {" "}güncellenmektedir.
      </p>

      <div className="flex flex-col w-full gap-2 max-w-xs">
        {onGoToAllEvents && (
          <button
            onClick={onGoToAllEvents}
            className="touch-target w-full py-2 px-3 rounded-xl bg-navy-800 hover:bg-navy-750 border border-navy-700 text-xs font-semibold text-brand-primary flex items-center justify-center gap-1.5 transition active:scale-95"
          >
            <span>Tüm Maçları İncele</span>
            <ArrowRight className="w-3.5 h-3.5" />
          </button>
        )}
        {onRefresh && (
          <button
            onClick={onRefresh}
            disabled={refreshing}
            className="touch-target w-full py-2 px-3 rounded-xl bg-transparent hover:bg-navy-850 text-xs font-medium text-slate-400 flex items-center justify-center gap-1.5 transition"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? "animate-spin" : ""}`} />
            <span>{refreshing ? "Veriler taranıyor..." : "Verileri Yeniden Tara"}</span>
          </button>
        )}
        {refreshError && <p role="alert" className="text-[11px] text-rose-300">{refreshError}</p>}
      </div>
    </div>
  );
};
