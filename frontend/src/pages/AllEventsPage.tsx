import React, { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Search, SlidersHorizontal } from "lucide-react";
import clsx from "clsx";
import { api } from "../api/client";
import { TimeScope } from "../types/api";
import { groupEvents } from "../features/taxonomy/groupEvents";
import { CountryLeagueAccordion } from "../components/events/CountryLeagueAccordion";
import { TaxonomyFilterSheet } from "../components/taxonomy/TaxonomyFilterSheet";
import { useDebouncedValue } from "../features/common/useDebouncedValue";
import { useSavedTaxonomyFilters } from "../features/taxonomy/useSavedTaxonomyFilters";

const TIME_SCOPES: ReadonlyArray<{ value: TimeScope; label: string }> = [
  { value: "today", label: "Bugün" },
  { value: "tomorrow", label: "Yarın" },
  { value: "all", label: "Tümü" },
];

const SPORT_FILTERS: ReadonlyArray<{ value: "all" | "football" | "basketball"; label: string }> = [
  { value: "all", label: "Tüm Sporlar" },
  { value: "football", label: "⚽ Futbol" },
  { value: "basketball", label: "🏀 Euroleague" },
];

interface AllEventsPageProps {
  userId: number;
}

export const AllEventsPage: React.FC<AllEventsPageProps> = ({ userId }) => {
  const [search, setSearch] = useState("");
  const [timeScope, setTimeScope] = useState<TimeScope>("today");
  const [selectedSport, setSelectedSport] = useState<"football" | "basketball" | "all">("all");
  // Server-backed, per-user, per-scope saved filter selection (Task 9). Kept
  // entirely separate from Opportunities, which uses its own hook instance
  // scoped to "opportunities".
  const taxonomyFilters = useSavedTaxonomyFilters(userId, "events");
  const [filterSheetOpen, setFilterSheetOpen] = useState(false);

  const trimmedSearch = search.trim();
  const debouncedSearch = useDebouncedValue(trimmedSearch, 300);
  const selectedFilterCount =
    taxonomyFilters.filters.country_ids.length + taxonomyFilters.filters.league_ids.length;
  // A time scope other than the default also narrows results, so an empty response
  // under any of these counts as "filtered empty" rather than "nothing collected".
  const hasActiveNarrowing = trimmedSearch !== "" || selectedFilterCount > 0 || timeScope !== "today" || selectedSport !== "all";

  const {
    data: events,
    isLoading,
    isError,
    error,
  } = useQuery({
    queryKey: [
      "events",
      debouncedSearch,
      timeScope,
      selectedSport,
      taxonomyFilters.queryOverride.country_ids,
      taxonomyFilters.queryOverride.league_ids,
      taxonomyFilters.queryOverride.use_saved_filters,
    ],
    queryFn: () =>
      api.getEvents({
        search: debouncedSearch || undefined,
        time_scope: timeScope,
        sport: selectedSport,
        ...taxonomyFilters.queryOverride,
      }),
    refetchInterval: 15000,
  });

  const groups = useMemo(() => groupEvents(events ?? []), [events]);

  const clearAllNarrowing = () => {
    setSearch("");
    setTimeScope("today");
    setSelectedSport("all");
    taxonomyFilters.onChange({ country_ids: [], league_ids: [] });
  };

  // React Query keeps the last successful `data` for this queryKey visible while a
  // background refetch (e.g. the 15s poll) fails - `events` is only undefined when
  // no successful fetch has ever landed for the current search/scope/filter combo.
  const hasStaleDataDuringError = isError && events !== undefined;
  const hasNoDataAtAll = isError && events === undefined;

  return (
    <div className="space-y-3 pb-8">
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <input
            type="text"
            placeholder="Takım veya lig ara..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full bg-navy-900 border border-navy-800 rounded-xl py-2.5 pl-9 pr-3 text-xs text-white placeholder-slate-500 outline-none focus:border-brand-primary transition"
          />
          <Search className="w-4 h-4 text-slate-500 absolute left-3 top-3" />
        </div>
        <button
          type="button"
          onClick={() => setFilterSheetOpen(true)}
          className="touch-target relative flex items-center gap-1.5 px-3 rounded-xl bg-navy-900 border border-navy-800 text-xs font-semibold text-slate-300"
        >
          <SlidersHorizontal className="w-4 h-4" />
          <span>Filtrele</span>
          {selectedFilterCount > 0 && (
            <span className="ml-0.5 inline-flex items-center justify-center min-w-[16px] h-4 px-1 rounded-full bg-brand-primary text-navy-950 text-[10px] font-bold">
              {selectedFilterCount}
            </span>
          )}
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-2 justify-between">
        <div className="flex items-center gap-1.5">
          {SPORT_FILTERS.map((sp) => (
            <button
              key={sp.value}
              type="button"
              onClick={() => {
                setSelectedSport(sp.value);
                if (sp.value === "basketball" && timeScope === "today") {
                  setTimeScope("all");
                }
              }}
              className={clsx(
                "touch-target px-3 rounded-full text-xs font-medium transition border",
                selectedSport === sp.value
                  ? "bg-brand-primary text-navy-950 border-brand-primary font-semibold"
                  : "bg-navy-850 text-slate-400 border-navy-750 hover:text-white"
              )}
            >
              {sp.label}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-1.5">
          {TIME_SCOPES.map((scope) => (
            <button
              key={scope.value}
              type="button"
              onClick={() => setTimeScope(scope.value)}
              className={clsx(
                "touch-target px-2.5 rounded-full text-xs font-medium transition border",
                timeScope === scope.value
                  ? "bg-brand-primary/20 text-brand-primary border-brand-primary/40 font-semibold"
                  : "bg-navy-850 text-slate-400 border-navy-750 hover:text-white"
              )}
            >
              {scope.label}
            </button>
          ))}
        </div>
      </div>

      {hasStaleDataDuringError && (
        <div
          role="alert"
          className="text-[11px] text-amber-300 bg-amber-950/30 border border-amber-800/50 rounded-lg px-3 py-2"
        >
          Veriler güncellenemedi, son bilinen veriler gösteriliyor.
        </div>
      )}

      {taxonomyFilters.isSaveError && (
        <div
          role="alert"
          className="flex items-center justify-between gap-2 rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-[11px] text-rose-300"
        >
          <span>Filtre tercihleri kaydedilemedi.</span>
          <button
            type="button"
            onClick={taxonomyFilters.retry}
            className="touch-target rounded-xl border border-rose-500/30 px-3 font-semibold"
          >
            Tekrar Dene
          </button>
        </div>
      )}

      {isLoading ? (
        <div className="space-y-2.5" data-testid="events-loading">
          {[1, 2, 3, 4].map((i) => (
            <div
              key={i}
              className="bg-navy-900/60 rounded-xl p-3 border border-navy-800 animate-pulse h-24"
            />
          ))}
        </div>
      ) : hasNoDataAtAll ? (
        <div className="text-center py-8 text-xs text-slate-500 space-y-1">
          <p className="text-rose-300 font-semibold">Veriler yüklenemedi.</p>
          {error instanceof Error && <p>{error.message}</p>}
        </div>
      ) : groups.length === 0 ? (
        hasActiveNarrowing ? (
          <div className="text-center py-8 text-xs text-slate-500 space-y-2">
            <p>Filtrelere uygun maç bulunamadı.</p>
            <button
              type="button"
              onClick={clearAllNarrowing}
              className="touch-target px-3 rounded-xl bg-navy-900 border border-navy-800 text-[11px] font-semibold text-brand-primary"
            >
              Filtreleri Temizle
            </button>
          </div>
        ) : (
          <div className="text-center py-8 text-xs text-slate-500">Henüz veri toplanmadı.</div>
        )
      ) : (
        <CountryLeagueAccordion groups={groups} searchActive={debouncedSearch !== ""} />
      )}

      <TaxonomyFilterSheet
        open={filterSheetOpen}
        onClose={() => setFilterSheetOpen(false)}
        filters={taxonomyFilters.filters}
        onChange={taxonomyFilters.onChange}
      />
    </div>
  );
};
