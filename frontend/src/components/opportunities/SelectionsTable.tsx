import React from "react";
import { CalculatedOpportunity, OutcomeName } from "../../types/api";
import { MoneyDisplay } from "../common/MoneyDisplay";
import { SourceBadge } from "../common/StatusBadge";
import { Sparkles, XCircle } from "lucide-react";

export const SelectionsTable: React.FC<{ opportunity: CalculatedOpportunity }> = ({
  opportunity,
}) => {
  const { selections, market_type } = opportunity;

  const getSelectionLabel = (key: string): string => {
    if (market_type === "OU25") {
      if (key === "OVER") return "2.5 Gol Üst (Over)";
      if (key === "UNDER") return "2.5 Gol Alt (Under)";
    }
    if (key === "HOME") return "Ev Sahibi (MS 1)";
    if (key === "DRAW") return "Beraberlik (X)";
    if (key === "AWAY") return "Deplasman (MS 2)";
    return key;
  };

  return (
    <div className="bg-navy-900 rounded-2xl border border-navy-800 p-3.5 mb-3 shadow-md">
      <h3 className="text-xs font-bold uppercase tracking-wider text-slate-300 mb-2.5">
        Seçilen Bahisler ve Kupon Tutarları
      </h3>

      <div className="space-y-2.5">
        {Object.entries(selections).map(([key, item]) => {
          const label = getSelectionLabel(key);
          const oddsNum = parseFloat(item.odds.toString()).toFixed(2);

          return (
            <div
              key={key}
              className="bg-navy-950/80 rounded-xl p-3 border border-navy-850 space-y-2"
            >
              <div className="flex items-start justify-between">
                <div className="flex flex-col">
                  <span className="text-xs font-bold text-white">{label}</span>
                  <div className="flex items-center gap-1.5 mt-1">
                    <SourceBadge source={item.source} />
                    <span className="text-xs font-mono font-bold text-slate-200">
                      @{oddsNum}
                    </span>
                  </div>
                </div>

                <div className="flex flex-col items-end">
                  <div className="font-mono text-sm font-bold text-white">
                    <MoneyDisplay cents={item.stake_cents} />
                  </div>
                  <div className="mt-0.5">
                    {item.cashback_eligible ? (
                      <span className="inline-flex items-center gap-0.5 text-[10px] font-semibold text-emerald-400">
                        <Sparkles className="w-2.5 h-2.5" />
                        <span>%10 KB uygun</span>
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-0.5 text-[10px] font-medium text-slate-500">
                        <XCircle className="w-2.5 h-2.5" />
                        <span>Bonus yok</span>
                      </span>
                    )}
                  </div>
                </div>
              </div>

              <div className="pt-2 border-t border-navy-850/80 flex items-center justify-between">
                <span className="text-[10px] text-slate-400">
                  Önerilen Yatırım:{" "}
                  <span className="text-white font-mono font-bold">
                    {(item.stake_cents / 100).toFixed(2)} TL
                  </span>
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
