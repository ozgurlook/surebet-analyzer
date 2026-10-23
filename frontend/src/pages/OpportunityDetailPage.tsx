import React, { useMemo } from "react";
import { ArrowLeft, Clock, Sparkles, ShieldAlert, Share2, ExternalLink } from "lucide-react";
import { CalculatedOpportunity, SourceName } from "../types/api";
import { MoneyDisplay } from "../components/common/MoneyDisplay";
import { SelectionsTable } from "../components/opportunities/SelectionsTable";
import { ScenarioCards } from "../components/opportunities/ScenarioCards";
import { StandardVsAdjustedComparison } from "../components/opportunities/StandardVsAdjustedComparison";
import { CashbackAssumptionPanel } from "../components/opportunities/CashbackAssumptionPanel";
import { SafetyDisclaimer } from "../components/opportunities/SafetyDisclaimer";
import clsx from "clsx";

interface OpportunityDetailPageProps {
  opportunity: CalculatedOpportunity;
  onBack: () => void;
}

export const OpportunityDetailPage: React.FC<OpportunityDetailPageProps> = ({
  opportunity,
  onBack,
}) => {
  const {
    competition,
    home,
    away,
    kickoff,
    budget_cents,
    min_adjusted_profit_cents,
    min_adjusted_profit_percent,
    oldest_quote_age_seconds,
    selections,
  } = opportunity;

  const formattedKickoff = new Date(kickoff).toLocaleDateString("tr-TR", {
    weekday: "short",
    day: "numeric",
    month: "long",
    hour: "2-digit",
    minute: "2-digit",
  });

  const isPositive = min_adjusted_profit_cents > 0;

  return (
    <div className="space-y-3 pb-8">
      {/* Top Back Navigation Bar */}
      <div className="flex items-center justify-between py-1">
        <button
          onClick={onBack}
          className="touch-target flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-navy-900 border border-navy-800 text-xs font-semibold text-slate-200 hover:text-white transition active:scale-95"
          aria-label="Geri Dön"
        >
          <ArrowLeft className="w-4 h-4 text-brand-primary" />
          <span>Fırsatlara Dön</span>
        </button>

        <span className="text-[10px] font-mono text-slate-400">
          Oran Yaşı: {oldest_quote_age_seconds}s
        </span>
      </div>

      {/* Match Header Hero Card */}
      <div className="bg-navy-900 rounded-2xl border border-navy-800 p-4 shadow-lg relative overflow-hidden">
        <div className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-1">
          {competition}
        </div>
        <h1 className="text-xl font-bold text-white tracking-tight leading-snug mb-1">
          {home} <span className="text-slate-500 font-normal">v</span> {away}
        </h1>
        <div className="text-xs text-slate-400 font-mono mb-4">
          {formattedKickoff}
        </div>

        {/* Big Profit Box */}
        <div className="bg-navy-950/90 rounded-xl p-3 border border-navy-850 flex items-center justify-between">
          <div>
            <span className="text-[10px] text-slate-400 uppercase tracking-wider font-semibold block">
              Hesaplanan Net Fark
            </span>
            <div className="flex items-baseline gap-1 mt-0.5">
              <span className="text-xl font-bold font-mono text-brand-positive">
                +{((min_adjusted_profit_cents) / 100).toFixed(2)} TL
              </span>
            </div>
          </div>

          <div className="text-right">
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-bold font-mono bg-emerald-500/20 text-emerald-300 border border-emerald-500/40">
              <Sparkles className="w-3 h-3" />
              <span>+%{min_adjusted_profit_percent.toFixed(2)}</span>
            </span>
            <span className="text-[10px] text-slate-400 block mt-1 font-mono">
              Bütçe: {budget_cents / 100} TL
            </span>
          </div>
        </div>
      </div>

      {/* 3 Selections Table */}
      <SelectionsTable opportunity={opportunity} />

      {/* 3 Scenarios Section */}
      <ScenarioCards opportunity={opportunity} />

      {/* Standard vs Adjusted Comparison */}
      <StandardVsAdjustedComparison opportunity={opportunity} />

      {/* Assumption Panel */}
      <CashbackAssumptionPanel />

      {/* Safety Disclaimer */}
      <SafetyDisclaimer />
    </div>
  );
};
