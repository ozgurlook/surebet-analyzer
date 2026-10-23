import React from "react";
import { AlertTriangle } from "lucide-react";

export const SafetyDisclaimer: React.FC = () => {
  return (
    <div className="bg-navy-950/60 rounded-xl border border-navy-850 p-3 mb-4 text-[10px] text-slate-400 flex items-start gap-2">
      <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
      <div className="space-y-1">
        <p className="font-semibold text-slate-300">
          Otomatik Bahis / Kupon Oynama Yapılmaz
        </p>
        <p className="leading-normal">
          Bu uygulama yalnızca kamuya açık oranları analiz eder. Bahis oynamadan önce ilgili bürolardaki güncel oranları ve kural/şartları mutlaka doğrulayınız.
        </p>
      </div>
    </div>
  );
};
