import React from "react";
import { CalculatedOpportunity, OutcomeName } from "../../types/api";
import { MoneyDisplay } from "../common/MoneyDisplay";
import { SourceBadge } from "../common/StatusBadge";
import { CheckCircle2, TrendingUp, Sparkles } from "lucide-react";
import clsx from "clsx";

export const ScenarioCards: React.FC<{ opportunity: CalculatedOpportunity }> = ({
  opportunity,
}) => {
  const { scenarios, budget_cents } = opportunity;
  const isTwoWay = opportunity.market_type && opportunity.market_type !== "1X2";

  const getScenarioMeta = (key: string, idx: number): { title: string; desc: string } => {
    if (opportunity.market_type === "OU25") {
      if (key === "OVER") return { title: `Senaryo ${idx + 1}: 2.5 Üst Biterse`, desc: "3 veya daha fazla gol olur" };
      if (key === "UNDER") return { title: `Senaryo ${idx + 1}: 2.5 Alt Biterse`, desc: "0, 1 veya 2 gol olur" };
    }
    if (key === "HOME") return { title: `Senaryo ${idx + 1}: Ev Sahibi Kazanırsa`, desc: "MS 1 bahsi kazanır" };
    if (key === "DRAW") return { title: `Senaryo ${idx + 1}: Beraberlik Biterse`, desc: "X bahsi kazanır" };
    if (key === "AWAY") return { title: `Senaryo ${idx + 1}: Deplasman Kazanırsa`, desc: "MS 2 bahsi kazanır" };
    return { title: `Senaryo ${idx + 1}: ${key}`, desc: `${key} bahsi kazanır` };
  };

  return (
    <div className="bg-navy-900 rounded-2xl border border-navy-800 p-3.5 mb-3 shadow-md">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-xs font-bold uppercase tracking-wider text-slate-300">
          {isTwoWay ? "2 Olası Senaryo" : "3 Olası Maç Sonucu Senaryosu"}
        </h3>
        <span className="text-[10px] text-slate-400 font-mono">Bütçe: {budget_cents / 100} TL</span>
      </div>

      <div className="space-y-3">
        {Object.entries(scenarios).map(([key, sc], idx) => {
          const { title } = getScenarioMeta(key, idx);
          const isPositive = sc.net_profit_cents > 0;

          return (
            <div
              key={key}
              className="bg-navy-950/80 rounded-xl p-3 border border-navy-850 space-y-2 relative overflow-hidden"
            >
              {/* Header */}
              <div className="flex items-start justify-between">
                <div>
                  <div className="text-xs font-bold text-white flex items-center gap-1.5">
                    <span>{title}</span>
                  </div>
                  <div className="text-[10px] text-slate-400 mt-0.5 flex items-center gap-1">
                    <span>Kazanan:</span>
                    <SourceBadge source={sc.winning_source} className="py-0 px-1 text-[9px]" />
                    <span className="font-mono">@{parseFloat(sc.winning_odds.toString()).toFixed(2)}</span>
                  </div>
                </div>

                <div className="text-right">
                  <div
                    className={clsx(
                      "font-mono font-bold text-sm",
                      isPositive ? "text-brand-positive" : "text-brand-negative"
                    )}
                  >
                    <MoneyDisplay cents={sc.net_profit_cents} showSign />
                  </div>
                  <div
                    className={clsx(
                      "text-[10px] font-mono font-semibold",
                      isPositive ? "text-emerald-400/90" : "text-rose-400/90"
                    )}
                  >
                    {isPositive ? "+" : ""}%{sc.net_profit_percent.toFixed(2)} Net
                  </div>
                </div>
              </div>

              {/* Financial Calculation Breakdown */}
              <div className="pt-2 border-t border-navy-850/80 grid grid-cols-2 gap-y-1.5 text-[11px] font-mono">
                <div className="text-slate-400 flex items-center justify-between pr-2">
                  <span>Brüt Ödeme:</span>
                  <span className="font-bold text-slate-200">
                    <MoneyDisplay cents={sc.gross_payout_cents} size="sm" />
                  </span>
                </div>

                <div className="text-slate-400 flex items-center justify-between pl-2 border-l border-navy-850">
                  <span className="flex items-center gap-0.5 text-amber-300">
                    <Sparkles className="w-2.5 h-2.5" />
                    <span>%10 Cashback:</span>
                  </span>
                  <span className="font-bold text-amber-300">
                    +<MoneyDisplay cents={sc.cashback_cents} size="sm" />
                  </span>
                </div>

                <div className="text-slate-400 flex items-center justify-between pr-2">
                  <span>Toplam Ödeme:</span>
                  <span className="font-bold text-white">
                    <MoneyDisplay cents={sc.total_return_cents} size="sm" />
                  </span>
                </div>

                <div className="text-slate-400 flex items-center justify-between pl-2 border-l border-navy-850">
                  <span>Toplam Yatırım:</span>
                  <span className="font-bold text-slate-300">
                    <MoneyDisplay cents={sc.total_stake_cents} size="sm" />
                  </span>
                </div>
              </div>

              {sc.losing_eligible_stakes_cents > 0 && (
                <div className="text-[10px] text-slate-500 font-mono">
                  Kaybeden uygun kuponlar toplamı: {(sc.losing_eligible_stakes_cents / 100).toFixed(2)} TL
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};
