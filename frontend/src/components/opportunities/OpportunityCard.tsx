import React, { useState } from "react";
import { ChevronDown, ChevronRight, Clock, Layers, ShieldAlert, Sparkles, TrendingUp } from "lucide-react";
import { CalculatedOpportunity } from "../../types/api";
import { MoneyDisplay } from "../common/MoneyDisplay";
import { SourceBadge } from "../common/StatusBadge";
import { countryFlagEmoji } from "../../features/taxonomy/groupEvents";
import clsx from "clsx";

interface OpportunityCardProps {
  opportunity: CalculatedOpportunity;
  onClick: () => void;
  onSelectAlternative?: (alternative: CalculatedOpportunity) => void;
}
export const OpportunityCard: React.FC<OpportunityCardProps> = ({
  opportunity,
  onClick,
  onSelectAlternative,
}) => {
  const [showAlternatives, setShowAlternatives] = useState(false);
  const {
    competition,
    country_name,
    country_iso_code,
    league_name,
    home,
    away,
    kickoff,
    selections,
    min_adjusted_profit_cents,
    min_adjusted_profit_percent,
    min_standard_profit_cents,
    min_standard_profit_percent,
    oldest_quote_age_seconds,
  } = opportunity;

  const kickoffDate = new Date(kickoff);
  const formattedKickoff = kickoffDate.toLocaleDateString("tr-TR", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });

  const isPositive = min_adjusted_profit_cents > 0;
  const flag = countryFlagEmoji(country_iso_code);
  // Secondary taxonomy context, shown below the matchup title - never fabricated
  // when a match's country/league is not (yet) mapped.
  const taxonomyContext = [league_name, country_name].filter(Boolean).join(" · ");

  const isBasketball =
    opportunity.sport === "basketball" ||
    opportunity.market_type === "BASKETBALL_ML" ||
    opportunity.market_type === "BASKETBALL_AH";
  const isTwoWay = opportunity.market_type && opportunity.market_type !== "1X2";

  let marketBadge = "1X2";
  if (opportunity.market_type === "OU25") {
    marketBadge = "2.5 Alt/Üst";
  } else if (opportunity.market_type === "BASKETBALL_ML") {
    marketBadge = "Maç Kazananı";
  } else if (opportunity.market_type === "BASKETBALL_AH") {
    marketBadge = opportunity.market_label ?? "Asya Handikap";
  }

  const outcomeLabels: Record<string, string> = {
    HOME: isBasketball ? "1 (Ev)" : "MS 1",
    DRAW: "Beraberlik X",
    AWAY: isBasketball ? "2 (Dep)" : "MS 2",
    OVER: "2.5 Üst",
    UNDER: "2.5 Alt",
  };

  return (
    <div
      onClick={onClick}
      className="bg-navy-900/95 hover:bg-navy-850 active:scale-[0.99] transition-all duration-150 rounded-2xl border border-navy-800 hover:border-navy-700 p-4 mb-3 shadow-md cursor-pointer group relative overflow-hidden"
    >
      {/* Top Bar: League, Kickoff, Freshness */}
      <div className="flex items-center justify-between text-[11px] text-slate-400 mb-2">
        <span className="font-medium truncate max-w-[180px] text-slate-300">
          {competition}
        </span>
        <div className="flex items-center gap-2 font-mono text-[10px]">
          <span>{formattedKickoff}</span>
          <span>•</span>
          <span
            className={clsx(
              oldest_quote_age_seconds > 45 ? "text-amber-400" : "text-slate-400"
            )}
          >
            {oldest_quote_age_seconds}s önce
          </span>
        </div>
      </div>

      {/* Match Title & Primary Return Banner */}
      <div className="flex items-start justify-between gap-2 mb-3">
        <div className="flex-1 min-w-0">
          <h2 className="text-base font-bold text-white tracking-tight truncate">
            {home} <span className="text-slate-500 font-normal">v</span> {away}
          </h2>
          {taxonomyContext && (
            <div className="flex items-center gap-1 mt-0.5 text-[10px] font-normal text-slate-400 truncate">
              {flag && <span aria-hidden="true">{flag}</span>}
              <span className="truncate">{taxonomyContext}</span>
            </div>
          )}
          <div className="flex items-center gap-1.5 mt-0.5">
            {isBasketball && (
              <span
                data-testid="basketball-badge"
                className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-orange-500/15 text-orange-300 border border-orange-500/30"
              >
                <span>🏀</span>
                <span>Euroleague</span>
              </span>
            )}
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-sky-500/15 text-sky-300 border border-sky-500/30">
              {marketBadge}
            </span>
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-amber-500/15 text-amber-300 border border-amber-500/30">
              <Sparkles className="w-2.5 h-2.5" />
              <span>%10 KB Dahil</span>
            </span>
            {min_standard_profit_cents <= 0 && (
              <span
                data-testid="cashback-dependent-warning"
                title="Oranların kendisi pozitif fark vermiyor; fark yalnızca %10 cashback varsayımından geliyor."
                className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-rose-500/15 text-rose-300 border border-rose-500/30"
              >
                <ShieldAlert className="w-2.5 h-2.5" />
                <span>
                  Arbitraj değil · Standart {min_standard_profit_percent > 0 ? "+" : ""}
                  {min_standard_profit_percent}%
                </span>
              </span>
            )}
          </div>
        </div>

        {/* Primary Profit Pill */}
        <div className="text-right shrink-0">
          <div
            className={clsx(
              "font-mono font-bold text-lg tracking-tight",
              isPositive ? "text-brand-positive" : "text-brand-negative"
            )}
          >
            <MoneyDisplay
              cents={min_adjusted_profit_cents}
              showSign
              size="lg"
              className={isPositive ? "text-brand-positive" : "text-brand-negative"}
            />
          </div>
          <div
            className={clsx(
              "text-xs font-mono font-semibold",
              isPositive ? "text-emerald-400/90" : "text-rose-400/90"
            )}
          >
            {isPositive ? "+" : ""}
            %{min_adjusted_profit_percent.toFixed(2)} Net Fark
          </div>
        </div>
      </div>

      {/* Outcome Columns: 2 cols for 2-way, 3 cols for 1X2 */}
      <div
        className={clsx(
          "grid gap-1.5 bg-navy-950/80 rounded-xl p-2 border border-navy-850 text-center",
          isTwoWay ? "grid-cols-2" : "grid-cols-3"
        )}
      >
        {Object.entries(selections).map(([outcomeKey, sel], idx, arr) => (
          <div
            key={outcomeKey}
            className={clsx(
              "flex flex-col items-center",
              !isTwoWay && idx === 1 && "border-x border-navy-850",
              isTwoWay && idx === 1 && "border-l border-navy-850"
            )}
          >
            <span className="text-[10px] font-bold text-slate-400 mb-0.5">
              {outcomeLabels[outcomeKey] || outcomeKey}
            </span>
            <div className="flex items-center gap-1 mb-1">
              <SourceBadge source={sel.source} />
            </div>
            <span className="text-xs font-mono font-bold text-white">
              @{parseFloat(sel.odds.toString()).toFixed(2)}
            </span>
            <span className="text-[10px] font-mono text-slate-400 mt-0.5">
              <MoneyDisplay cents={sel.stake_cents} size="sm" />
            </span>
          </div>
        ))}
      </div>

      {/* Alternatives Accordion */}
      {opportunity.alternatives && opportunity.alternatives.length > 0 && (
        <div className="mt-2.5 pt-2 border-t border-navy-850/60" onClick={(e) => e.stopPropagation()}>
          <button
            type="button"
            data-testid="toggle-alternatives-button"
            onClick={() => setShowAlternatives((prev) => !prev)}
            className="w-full flex items-center justify-between py-1.5 px-2.5 rounded-xl bg-navy-950/60 hover:bg-navy-950 border border-navy-800/80 text-[11px] font-semibold text-slate-300 transition active:scale-[0.99]"
          >
            <div className="flex items-center gap-1.5">
              <Layers className="w-3.5 h-3.5 text-brand-primary" />
              <span>Diğer Kombinasyonlar ({opportunity.alternatives.length})</span>
            </div>
            <div className="flex items-center gap-1 text-[10px] text-slate-400 font-mono">
              <span>{showAlternatives ? "Gizle" : "Görüntüle"}</span>
              <ChevronDown
                className={clsx(
                  "w-3.5 h-3.5 transition-transform duration-200",
                  showAlternatives && "rotate-180"
                )}
              />
            </div>
          </button>

          {showAlternatives && (
            <div className="mt-2 space-y-1.5">
              {opportunity.alternatives.map((alt) => {
                const altIsPositive = alt.min_adjusted_profit_cents > 0;
                return (
                  <div
                    key={alt.opportunity_id}
                    onClick={() => onSelectAlternative ? onSelectAlternative(alt) : onClick()}
                    className="flex items-center justify-between p-2 rounded-xl bg-navy-950/90 hover:bg-navy-800/80 border border-navy-850 hover:border-navy-700 transition cursor-pointer text-[11px]"
                  >
                    {/* Sources preview */}
                    <div className="flex items-center gap-1.5 flex-wrap flex-1 min-w-0 mr-2">
                      {Object.entries(alt.selections).map(([key, sel], i, arr) => (
                        <React.Fragment key={key}>
                          <div className="flex items-center gap-1 font-mono text-[10px]">
                            <span className="text-slate-400 font-semibold">
                              {outcomeLabels[key] || key}:
                            </span>
                            <SourceBadge source={sel.source} />
                            <span className="text-slate-200">
                              @{parseFloat(sel.odds.toString()).toFixed(2)}
                            </span>
                          </div>
                          {i < arr.length - 1 && <span className="text-slate-600">•</span>}
                        </React.Fragment>
                      ))}
                    </div>

                    {/* Profit badge */}
                    <div className="text-right shrink-0">
                      <div
                        className={clsx(
                          "font-mono font-bold text-xs",
                          altIsPositive ? "text-emerald-400" : "text-rose-400"
                        )}
                      >
                        {altIsPositive ? "+" : ""}
                        {alt.min_adjusted_profit_percent.toFixed(2)}%
                      </div>
                      <div className="text-[9px] font-mono text-slate-400">
                        {altIsPositive ? (
                          <span>+{((alt.min_adjusted_profit_cents) / 100).toFixed(0)} TL</span>
                        ) : (
                          <span className="text-rose-400/80">Zarar ({((alt.min_adjusted_profit_cents) / 100).toFixed(0)} TL)</span>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Card Footer */}
      <div className="mt-2.5 pt-2 border-t border-navy-850/60 flex items-center justify-between text-[11px] text-slate-400">
        <span className="font-mono text-[10px]">
          Bütçe: <MoneyDisplay cents={opportunity.budget_cents} size="sm" />
        </span>
        <span className="flex items-center gap-0.5 text-brand-primary font-medium text-xs group-hover:translate-x-0.5 transition-transform">
          <span>Senaryoları İncele</span>
          <ChevronRight className="w-3.5 h-3.5" />
        </span>
      </div>
    </div>
  );
};
