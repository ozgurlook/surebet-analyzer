import React from "react";
import { Info } from "lucide-react";

export const CashbackAssumptionPanel: React.FC = () => {
  return (
    <div className="bg-navy-900/70 rounded-2xl border border-navy-800 p-3.5 mb-3 text-xs text-slate-300 space-y-2 shadow-sm">
      <div className="flex items-center gap-1.5 text-amber-300 font-bold text-xs">
        <Info className="w-4 h-4 text-amber-400 shrink-0" />
        <span>Bağımsız Kupon Başına Cashback Varsayımı</span>
      </div>
      <p className="text-[11px] text-slate-400 leading-relaxed">
        Bu analitik modelde, Kaynak B, C ve F'deki her kaybeden kupon, tutarının %10'u kadar doğrudan nakit iade (kayıp bonusu) üretir varsayımı kullanılmaktadır. Diğer kaynakların kuponlarına iade uygulanmaz.
      </p>
      <div className="bg-navy-950 p-2 rounded-lg font-mono text-[10px] text-slate-400 border border-navy-850">
        <code>cashback(r) = floor(0.10 * sum(iadeli_kaybeden_kuponlar(r)))</code>
      </div>
    </div>
  );
};
