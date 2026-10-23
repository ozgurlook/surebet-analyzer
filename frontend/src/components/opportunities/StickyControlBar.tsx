import React, { useState } from "react";
import { SlidersHorizontal, DollarSign, ToggleLeft, ToggleRight, Sparkles, Filter } from "lucide-react";
import clsx from "clsx";
import {
  MAX_BUDGET_LIRAS,
  MIN_BUDGET_LIRAS,
} from "../../features/opportunities/controlState";

interface StickyControlBarProps {
  budgetLiras: number;
  onBudgetChange: (liras: number) => void;
  includeSourceA: boolean;
  onIncludeSourceAChange: (include: boolean) => void;
  filterOption: "all" | "today" | "upcoming" | "high_margin";
  onFilterChange: (opt: "all" | "today" | "upcoming" | "high_margin") => void;
  /** Count of selected country/league taxonomy filters, shown as a badge on the
   * "Ülke/Lig" trigger. Independent from `filterOption` above. */
  selectedTaxonomyFilterCount: number;
  /** Opens the shared country/league TaxonomyFilterSheet, owned by the parent page. */
  onOpenTaxonomyFilters: () => void;
  selectedSport?: "football" | "basketball" | "all";
  onSportChange?: (sport: "football" | "basketball" | "all") => void;
}

export const StickyControlBar: React.FC<StickyControlBarProps> = ({
  budgetLiras,
  onBudgetChange,
  includeSourceA,
  onIncludeSourceAChange,
  filterOption,
  onFilterChange,
  selectedTaxonomyFilterCount,
  onOpenTaxonomyFilters,
  selectedSport = "all",
  onSportChange,
}) => {
  const [isEditingBudget, setIsEditingBudget] = useState(false);
  const [tempBudget, setTempBudget] = useState(budgetLiras.toString());
  const [budgetError, setBudgetError] = useState<string | null>(null);

  const quickBudgets = [500, 1000, 2500, 5000];

  const handleBudgetSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!/^\d+$/.test(tempBudget)) {
      setBudgetError("10 ile 10.000.000 TL arasında tam sayı girin");
      return;
    }

    const parsed = Number(tempBudget);
    if (
      !Number.isSafeInteger(parsed) ||
      parsed < MIN_BUDGET_LIRAS ||
      parsed > MAX_BUDGET_LIRAS
    ) {
      setBudgetError("10 ile 10.000.000 TL arasında tam sayı girin");
      return;
    }

    setBudgetError(null);
    onBudgetChange(parsed);
    setIsEditingBudget(false);
  };

  return (
    <div className="bg-navy-900/90 backdrop-blur-md rounded-2xl border border-navy-800 p-3.5 mb-4 shadow-lg space-y-3">
      {/* Budget & Source A Switch Row */}
      <div
        data-testid="budget-controls-row"
        className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"
      >
        {/* Budget Editor */}
        <div className="flex-1">
          <div className="text-[10px] text-slate-400 font-medium uppercase tracking-wider mb-1 flex items-center gap-1">
            <DollarSign className="w-3 h-3 text-brand-primary" />
            <span>Toplam Bütçe</span>
          </div>

          {isEditingBudget ? (
            <form onSubmit={handleBudgetSubmit} className="flex items-center gap-1.5">
              <input
                type="text"
                inputMode="numeric"
                aria-label="Toplam bütçe (TL)"
                value={tempBudget}
                onChange={(e) => {
                  setTempBudget(e.target.value);
                  setBudgetError(null);
                }}
                autoFocus
                onBlur={handleBudgetSubmit}
                className="w-24 bg-navy-950 border border-brand-primary rounded-lg px-2 py-1 text-sm font-bold font-mono text-white outline-none"
              />
              <span className="text-xs text-slate-400 font-bold">TL</span>
            </form>
          ) : (
            <div
              onClick={() => {
                setTempBudget(budgetLiras.toString());
                setBudgetError(null);
                setIsEditingBudget(true);
              }}
              className="cursor-pointer group flex items-baseline gap-1"
            >
              <span className="text-lg font-bold font-mono text-white tracking-tight group-hover:text-brand-primary transition">
                {budgetLiras.toLocaleString("tr-TR")}
              </span>
              <span className="text-xs font-semibold text-slate-400 group-hover:text-brand-primary transition">
                TL
              </span>
              <span className="text-[10px] text-brand-primary opacity-0 group-hover:opacity-100 transition ml-1 underline">
                düzenle
              </span>
            </div>
          )}
          {budgetError && (
            <p role="alert" className="mt-1 text-[10px] font-medium text-red-300">
              {budgetError}
            </p>
          )}
        </div>

        {/* Quick Budget Chips */}
        <div data-testid="quick-budgets" className="grid w-full grid-cols-4 gap-1 sm:flex sm:w-auto sm:items-center">
          {quickBudgets.map((amt) => (
            <button
              key={amt}
              onClick={() => {
                setBudgetError(null);
                onBudgetChange(amt);
              }}
              className={clsx(
                "px-2 py-1 rounded-md text-[11px] font-mono font-medium transition active:scale-95",
                budgetLiras === amt
                  ? "bg-brand-primary/20 text-brand-primary border border-brand-primary/40 font-bold"
                  : "bg-navy-850 hover:bg-navy-800 text-slate-400 hover:text-slate-200 border border-navy-750"
              )}
            >
              {amt >= 1000 ? `${amt / 1000}k` : amt}
            </button>
          ))}
        </div>
      </div>

      {/* Source A Switch Row */}
      <div className="pt-2 border-t border-navy-800/80 flex items-start justify-between gap-3">
        <div data-testid="source_a-copy" className="min-w-0 flex-1 flex flex-col">
          <span className="text-xs font-medium text-slate-200">
            MS1/MS2'de Kaynak A oranlarını kullan
          </span>
          <span className="text-[10px] text-slate-400">
            {includeSourceA
              ? "Kaynak A oranları dahil (Kaynak A kuponuna cashback yok)"
              : "Yalnızca Kaynak C ve Kaynak B yan oranları aktif"}
          </span>
        </div>

        <button
          onClick={() => onIncludeSourceAChange(!includeSourceA)}
          className={clsx(
            "touch-target shrink-0 flex items-center gap-1.5 px-2.5 py-1 rounded-xl text-xs font-semibold transition active:scale-95 border",
            includeSourceA
              ? "bg-amber-500/20 text-amber-300 border-amber-500/40"
              : "bg-navy-850 text-slate-400 border-navy-700"
          )}
          aria-label="Kaynak A yan oran switch"
        >
          {includeSourceA ? (
            <ToggleRight className="w-5 h-5 text-amber-400" />
          ) : (
            <ToggleLeft className="w-5 h-5 text-slate-500" />
          )}
          <span>{includeSourceA ? "Açık" : "Kapalı"}</span>
        </button>
      </div>

      {/* Sport Segmented Filter */}
      {onSportChange && (
        <div className="pt-2 border-t border-navy-800/80 flex items-center justify-center">
          <div
            data-testid="sport-segmented-filter"
            className="bg-navy-950/90 p-1 rounded-xl border border-navy-800 flex items-center w-full gap-1"
          >
            <button
              type="button"
              data-testid="sport-filter-football"
              onClick={() => onSportChange("football")}
              className={clsx(
                "touch-target flex-1 py-1.5 px-2 rounded-lg text-xs font-semibold transition flex items-center justify-center gap-1.5",
                selectedSport === "football"
                  ? "bg-brand-primary text-navy-950 shadow-sm font-bold"
                  : "text-slate-400 hover:text-white"
              )}
            >
              <span>⚽</span>
              <span>Futbol</span>
            </button>
            <button
              type="button"
              data-testid="sport-filter-basketball"
              onClick={() => onSportChange("basketball")}
              className={clsx(
                "touch-target flex-1 py-1.5 px-2 rounded-lg text-xs font-semibold transition flex items-center justify-center gap-1.5",
                selectedSport === "basketball"
                  ? "bg-brand-primary text-navy-950 shadow-sm font-bold"
                  : "text-slate-400 hover:text-white"
              )}
            >
              <span>🏀</span>
              <span>Basketbol Euroleague</span>
            </button>
            <button
              type="button"
              data-testid="sport-filter-all"
              onClick={() => onSportChange("all")}
              className={clsx(
                "touch-target py-1.5 px-3 rounded-lg text-xs font-semibold transition flex items-center justify-center gap-1",
                selectedSport === "all" || !selectedSport
                  ? "bg-brand-primary text-navy-950 shadow-sm font-bold"
                  : "text-slate-400 hover:text-white"
              )}
            >
              <span>Tümü</span>
            </button>
          </div>
        </div>
      )}

      {/* Filter Chips */}
      <div className="min-w-0 w-full pt-2 border-t border-navy-800/80 flex items-center gap-1.5 overflow-x-auto pb-0.5 scrollbar-none">
        <button
          type="button"
          onClick={onOpenTaxonomyFilters}
          className="touch-target shrink-0 relative flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium whitespace-nowrap transition active:scale-95 border bg-navy-850 text-slate-400 border-navy-750 hover:text-slate-200"
        >
          <SlidersHorizontal className="w-3.5 h-3.5" />
          <span>Ülke/Lig</span>
          {selectedTaxonomyFilterCount > 0 && (
            <span className="ml-0.5 inline-flex items-center justify-center min-w-[16px] h-4 px-1 rounded-full bg-brand-primary text-navy-950 text-[10px] font-bold">
              {selectedTaxonomyFilterCount}
            </span>
          )}
        </button>
        <button
          onClick={() => onFilterChange("all")}
          className={clsx(
            "shrink-0 px-2.5 py-1 rounded-full text-xs font-medium whitespace-nowrap transition active:scale-95 border",
            filterOption === "all"
              ? "bg-brand-primary/20 text-brand-primary border-brand-primary/40 font-semibold"
              : "bg-navy-850 text-slate-400 border-navy-750 hover:text-slate-200"
          )}
        >
          Tüm Fırsatlar
        </button>
        <button
          onClick={() => onFilterChange("today")}
          className={clsx(
            "shrink-0 px-2.5 py-1 rounded-full text-xs font-medium whitespace-nowrap transition active:scale-95 border",
            filterOption === "today"
              ? "bg-brand-primary/20 text-brand-primary border-brand-primary/40 font-semibold"
              : "bg-navy-850 text-slate-400 border-navy-750 hover:text-slate-200"
          )}
        >
          Bugün
        </button>
        <button
          onClick={() => onFilterChange("upcoming")}
          className={clsx(
            "shrink-0 px-2.5 py-1 rounded-full text-xs font-medium whitespace-nowrap transition active:scale-95 border",
            filterOption === "upcoming"
              ? "bg-brand-primary/20 text-brand-primary border-brand-primary/40 font-semibold"
              : "bg-navy-850 text-slate-400 border-navy-750 hover:text-slate-200"
          )}
        >
          Yaklaşan
        </button>
        <button
          onClick={() => onFilterChange("high_margin")}
          className={clsx(
            "shrink-0 px-2.5 py-1 rounded-full text-xs font-medium whitespace-nowrap transition active:scale-95 border flex items-center gap-1",
            filterOption === "high_margin"
              ? "bg-emerald-500/20 text-emerald-300 border-emerald-500/40 font-semibold"
              : "bg-navy-850 text-slate-400 border-navy-750 hover:text-slate-200"
          )}
        >
          <Sparkles className="w-3 h-3 text-emerald-400" />
          <span>%2+ Fark</span>
        </button>
      </div>
    </div>
  );
};
