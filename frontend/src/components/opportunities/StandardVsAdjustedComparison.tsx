import React from "react";
import { CalculatedOpportunity } from "../../types/api";
import { MoneyDisplay } from "../common/MoneyDisplay";
import { ArrowRight, Sparkles } from "lucide-react";
import clsx from "clsx";

export const StandardVsAdjustedComparison: React.FC<{
  opportunity: CalculatedOpportunity;
}> = ({ opportunity }) => {
  const {
    min_standard_profit_cents,
    min_standard_profit_percent,
    min_adjusted_profit_cents,
    min_adjusted_profit_percent,
  } = opportunity;

  return (
    <div className="bg-navy-900 rounded-2xl border border-navy-800 p-3.5 mb-3 shadow-md">
      <h3 className="text-xs font-bold uppercase tracking-wider text-slate-300 mb-2.5">
        Standart ve Bonuslu Hesap Karşılaştırması
      </h3>

      <div className="grid grid-cols-2 gap-2">
        {/* Standard Profit */}
        <div className="bg-navy-950/70 rounded-xl p-2.5 border border-navy-850">
          <span className="text-[10px] font-semibold text-slate-400 block mb-1">
            Standart Arbitraj
          </span>
          <div
            className={clsx(
              "font-mono font-bold text-sm",
              min_standard_profit_cents > 0 ? "text-emerald-400" : "text-slate-300"
            )}
          >
            <MoneyDisplay cents={min_standard_profit_cents} showSign />
          </div>
          <span className="text-[10px] font-mono text-slate-400">
            %{min_standard_profit_percent.toFixed(2)} min
          </span>
        </div>

        {/* Adjusted Profit */}
        <div className="bg-emerald-950/20 rounded-xl p-2.5 border border-emerald-500/30 relative">
          <div className="flex items-center justify-between mb-1">
            <span className="text-[10px] font-bold text-emerald-300 flex items-center gap-1">
              <Sparkles className="w-2.5 h-2.5" />
              <span>%10 Cashbackli</span>
            </span>
          </div>
          <div className="font-mono font-bold text-sm text-brand-positive">
            <MoneyDisplay cents={min_adjusted_profit_cents} showSign />
          </div>
          <span className="text-[10px] font-mono text-emerald-400 font-semibold">
            %{min_adjusted_profit_percent.toFixed(2)} Net Fark
          </span>
        </div>
      </div>
    </div>
  );
};
