import React from "react";
import clsx from "clsx";
import { EventListItem, SourceName } from "../../types/api";
import { countryFlagEmoji } from "../../features/taxonomy/groupEvents";
import { SOURCE_LABELS } from "../../features/sources/sourceLabels";

const SOURCES = Object.keys(SOURCE_LABELS) as SourceName[];

interface EventCardProps {
  event: EventListItem;
}

/** One match card: league/country context, home v away, best-margin badge, and the
 * three-source odds grid. Preserved verbatim from the original flat All Matches view -
 * only the league/country context line and flag are new. Never invents a flag for a
 * missing ISO code (International, Unknown, or an unmapped/raw competition label). */
export const EventCard: React.FC<EventCardProps> = ({ event }) => {
  const formattedKickoff = new Date(event.kickoff).toLocaleDateString("tr-TR", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });

  const contextLabel = event.league_name ?? event.competition;
  const flag = countryFlagEmoji(event.country_iso_code);

  return (
    <div className="bg-navy-900 rounded-xl border border-navy-800 p-3 shadow-sm space-y-2">
      <div className="flex items-center justify-between text-[10px] text-slate-400">
        <span className="truncate max-w-[200px] text-slate-300 font-medium flex items-center gap-1">
          {flag && <span aria-hidden="true">{flag}</span>}
          {contextLabel}
        </span>
        <span className="font-mono">{formattedKickoff}</span>
      </div>

      <div className="flex items-center justify-between">
        <h4 className="text-sm font-bold text-white truncate max-w-[220px]">
          {event.home} v {event.away}
        </h4>
        {event.best_margin_percent !== null && (
          <span
            className={clsx(
              "text-xs font-mono font-bold px-1.5 py-0.5 rounded",
              event.is_positive
                ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/40"
                : "text-slate-400 bg-navy-950"
            )}
          >
            {event.best_margin_percent > 0 ? "+" : ""}
            %{event.best_margin_percent.toFixed(2)}
          </span>
        )}
      </div>

      {/* Multi-source Odds Grid */}
      <div className="pt-2 border-t border-navy-850 grid grid-cols-2 md:grid-cols-4 gap-1 text-[10px] font-mono text-center">
        {SOURCES.map((src) => {
          const isBasketball = event.sport === "basketball";
          const hOdds = event.quotes[`${src}:HOME`];
          const dOdds = event.quotes[`${src}:DRAW`];
          const aOdds = event.quotes[`${src}:AWAY`];

          const hasData = isBasketball ? (hOdds && aOdds) : (hOdds && dOdds && aOdds);

          return (
            <div
              key={src}
              className={clsx(
                "rounded-lg p-1.5 border",
                hasData
                  ? "bg-navy-950/80 border-navy-800"
                  : "bg-navy-950/30 border-navy-900 opacity-40"
              )}
            >
              <div className="text-[9px] font-semibold text-slate-400 uppercase mb-1">
                <span>{SOURCE_LABELS[src]}</span>
              </div>
              {hasData ? (
                <div className={clsx("grid text-slate-200 font-bold", isBasketball ? "grid-cols-2" : "grid-cols-3")}>
                  <span>{parseFloat(hOdds.toString()).toFixed(2)}</span>
                  {!isBasketball && dOdds && (
                    <span className="text-slate-400 font-normal">
                      {parseFloat(dOdds.toString()).toFixed(2)}
                    </span>
                  )}
                  <span>{parseFloat(aOdds.toString()).toFixed(2)}</span>
                </div>
              ) : (
                <span className="text-[9px] text-slate-500">Yok</span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};
