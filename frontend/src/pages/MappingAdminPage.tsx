import React, { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { api } from "../api/client";
import { UnmatchedItem } from "../types/api";
import {
  CONFLICT_REASON_LABEL,
  IDENTITY_KIND_LABEL,
  KIND_LABEL,
  MappingReviewSheet,
  REVIEW_STATE_LABEL,
} from "../components/taxonomy/MappingReviewSheet";
import { sourceLabel } from "../features/sources/sourceLabels";

// A manual mapping mutation affects the countries/leagues catalog, current
// events and opportunities (resolved at read time), and the dashboard
// summary - every successful mutation invalidates all of these.
const INVALIDATED_QUERY_KEYS = [
  ["mappings"],
  ["countries"],
  ["leagues"],
  ["events"],
  ["opportunities"],
  ["dashboard"],
];

interface MappingAdminPageProps {
  onBack: () => void;
}

function describeRawCountry(item: UnmatchedItem): string {
  return item.country_name ?? item.country_alias ?? item.country_id ?? "Ülke bilinmiyor";
}

export const MappingAdminPage: React.FC<MappingAdminPageProps> = ({ onBack }) => {
  const queryClient = useQueryClient();
  const [view, setView] = useState<"unmatched" | "mapped">("unmatched");
  const { data, isLoading, error } = useQuery({
    queryKey: ["mappings", view],
    queryFn: () =>
      view === "unmatched" ? api.getUnmatchedMappings() : api.getMappedMappings(),
  });
  const [selected, setSelected] = useState<UnmatchedItem | null>(null);
  const [conflictMessage, setConflictMessage] = useState<string | null>(null);

  const invalidateAll = () => {
    for (const queryKey of INVALIDATED_QUERY_KEYS) {
      void queryClient.invalidateQueries({ queryKey });
    }
  };

  const handleClose = () => {
    setSelected(null);
  };

  const handleMutated = () => {
    setConflictMessage(null);
    invalidateAll();
  };

  const handleConflict = () => {
    setSelected(null);
    invalidateAll();
    setConflictMessage(
      "Bu kayıt başka bir işlemle güncellendi. Liste yenilendi, lütfen tekrar deneyin."
    );
  };

  return (
    <div className="space-y-3 pb-8">
      <div className="flex items-center justify-between py-1">
        <button
          onClick={onBack}
          className="touch-target flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-navy-900 border border-navy-800 text-xs font-semibold text-slate-200 hover:text-white transition active:scale-95"
          aria-label="Geri Dön"
        >
          <ArrowLeft className="w-4 h-4 text-brand-primary" />
          <span>Ayarlara Dön</span>
        </button>
      </div>

      <div>
        <h2 className="text-sm font-bold text-white">Veri Eşleştirme</h2>
        <p className="text-[11px] text-slate-400">
          Bekleyen kayıtları inceleyin; mevcut eşleştirmeleri görüntüleyin veya geri alın.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-2" role="tablist" aria-label="Eşleştirme görünümü">
        <button
          type="button"
          role="tab"
          aria-selected={view === "unmatched"}
          onClick={() => {
            setSelected(null);
            setView("unmatched");
          }}
          className={`touch-target rounded-xl border px-3 py-2 text-xs font-semibold ${
            view === "unmatched"
              ? "border-brand-primary bg-brand-primary/15 text-white"
              : "border-navy-800 bg-navy-900 text-slate-400"
          }`}
        >
          Bekleyenler
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={view === "mapped"}
          onClick={() => {
            setSelected(null);
            setView("mapped");
          }}
          className={`touch-target rounded-xl border px-3 py-2 text-xs font-semibold ${
            view === "mapped"
              ? "border-brand-primary bg-brand-primary/15 text-white"
              : "border-navy-800 bg-navy-900 text-slate-400"
          }`}
        >
          Eşleştirilenler
        </button>
      </div>

      {conflictMessage && (
        <p role="alert" className="text-xs text-amber-300 bg-amber-500/10 border border-amber-500/30 rounded-xl p-3">
          {conflictMessage}
        </p>
      )}

      {isLoading && (
        <p data-testid="mapping-loading" className="text-xs text-slate-400">
          Yükleniyor...
        </p>
      )}

      {error && !isLoading && (
        <p className="text-xs text-rose-300">Eşleştirme listesi yüklenemedi.</p>
      )}

      {!isLoading && !error && (data?.length ?? 0) === 0 && (
        <p className="text-xs text-slate-400">
          {view === "unmatched" ? "Bekleyen eşleştirme yok." : "Eşleştirilmiş kayıt yok."}
        </p>
      )}

      {!isLoading && !error && (data?.length ?? 0) > 0 && (
        <ul className="space-y-2">
          {data!.map((item) => (
            <li key={`${item.source}:${item.competition_id}`}>
              <button
                type="button"
                onClick={() => setSelected(item)}
                data-testid={`mapping-item-${item.source}-${item.competition_id}`}
                className="touch-target w-full text-left bg-navy-900 rounded-2xl border border-navy-800 p-3 space-y-1 shadow-sm"
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs font-bold text-white">{item.competition_name}</span>
                  <span className="text-[10px] uppercase font-semibold text-slate-400">
                    {sourceLabel(item.source)}
                  </span>
                </div>
                <p className="text-[11px] text-slate-400">
                  {describeRawCountry(item)} · {KIND_LABEL[item.kind]}
                </p>
                <p className="text-[10px] text-slate-500">
                  {IDENTITY_KIND_LABEL[item.identity_kind]} ·{" "}
                  {item.confidence !== null ? `%${Math.round(item.confidence * 100)}` : "Güven yok"} ·{" "}
                  {item.review_state !== null
                    ? REVIEW_STATE_LABEL[item.review_state]
                    : "Eşleştirme yok"}
                </p>
                <p className="text-[10px] text-slate-500">
                  {view === "mapped" ? "Hedef" : "Öneri"}: {item.target ? item.target.display_name : "Yok"}
                </p>
                {item.conflict_reasons.length > 0 && (
                  <p className="text-[10px] text-amber-300">
                    {item.conflict_reasons
                      .map((reason) => CONFLICT_REASON_LABEL[reason] ?? reason)
                      .join(", ")}
                  </p>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}

      {selected && (
        <MappingReviewSheet
          item={selected}
          onClose={handleClose}
          onMutated={handleMutated}
          onConflict={handleConflict}
        />
      )}
    </div>
  );
};
