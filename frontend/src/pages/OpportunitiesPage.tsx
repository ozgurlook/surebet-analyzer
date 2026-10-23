import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { StickyControlBar } from "../components/opportunities/StickyControlBar";
import { OpportunityCard } from "../components/opportunities/OpportunityCard";
import { OpportunitySkeleton } from "../components/opportunities/OpportunitySkeleton";
import { EmptyState } from "../components/opportunities/EmptyState";
import { SafetyDisclaimer } from "../components/opportunities/SafetyDisclaimer";
import { TaxonomyFilterSheet } from "../components/taxonomy/TaxonomyFilterSheet";
import {
  loadOpportunityControls,
  saveOpportunityControls,
} from "../features/opportunities/controlState";
import {
  filterOpportunities,
  OpportunityQuickFilter,
} from "../features/opportunities/filterOpportunities";
import { useSavedTaxonomyFilters } from "../features/taxonomy/useSavedTaxonomyFilters";
import { CalculatedOpportunity } from "../types/api";

interface OpportunitiesPageProps {
  userId: number;
  onSelectOpportunity: (opportunity: CalculatedOpportunity) => void;
  onGoToAllEvents: () => void;
}

export const OpportunitiesPage: React.FC<OpportunitiesPageProps> = ({
  userId,
  onSelectOpportunity,
  onGoToAllEvents,
}) => {
  const queryClient = useQueryClient();
  const [initialControls] = useState(() => loadOpportunityControls(userId));
  const [budgetOverride, setBudgetOverride] = useState<number | null>(
    initialControls?.budgetLiras ?? null
  );
  const [includeSourceAOverride, setIncludeSourceAOverride] = useState<boolean | null>(
    initialControls?.includeSourceA ?? null
  );
  const [filterOption, setFilterOption] = useState<OpportunityQuickFilter>("all");
  const [selectedSport, setSelectedSport] = useState<"football" | "basketball" | "all">("all");
  // Server-backed, per-user, per-scope saved filter selection (Task 9). Kept
  // entirely separate from All Matches, which uses its own hook instance
  // scoped to "events".
  const taxonomyFilters = useSavedTaxonomyFilters(userId, "opportunities");
  const [filterSheetOpen, setFilterSheetOpen] = useState(false);

  const selectedTaxonomyFilterCount =
    taxonomyFilters.filters.country_ids.length + taxonomyFilters.filters.league_ids.length;
  const {
    data: settings,
    isLoading: settingsLoading,
    isError: settingsIsError,
    error: settingsError,
    refetch: refetchSettings,
  } = useQuery({
    queryKey: ["settings"],
    queryFn: api.getSettings,
  });
  const budgetLiras = budgetOverride ??
    (settings ? settings.personal.default_budget_cents / 100 : undefined);
  const includeSourceA = includeSourceAOverride ?? settings?.personal.include_source_a_match_sides;

  const {
    data: opportunities,
    isLoading,
    refetch,
    isRefetching,
    isError,
    error,
  } = useQuery({
    queryKey: [
      "opportunities",
      budgetLiras,
      includeSourceA,
      taxonomyFilters.queryOverride.country_ids,
      taxonomyFilters.queryOverride.league_ids,
      taxonomyFilters.queryOverride.use_saved_filters,
      selectedSport,
    ],
    queryFn: () =>
      api.getOpportunities({
        budget_cents: budgetLiras! * 100,
        include_source_a_match_sides: includeSourceA!,
        positive_only: true,
        sport: selectedSport === "all" ? undefined : selectedSport,
        ...taxonomyFilters.queryOverride,
      }),
    refetchInterval: 10000,
    enabled: budgetLiras !== undefined && includeSourceA !== undefined,
  });
  const refreshMutation = useMutation({
    mutationFn: api.refreshSources,
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["opportunities"] }),
        queryClient.invalidateQueries({ queryKey: ["events"] }),
        queryClient.invalidateQueries({ queryKey: ["sources"] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
      ]);
    },
  });

  const handleBudgetChange = (nextBudget: number) => {
    setBudgetOverride(nextBudget);
    saveOpportunityControls(userId, {
      budgetLiras: nextBudget,
      includeSourceA: includeSourceA!,
    });
  };

  const handleIncludeSourceAChange = (nextInclude: boolean) => {
    setIncludeSourceAOverride(nextInclude);
    saveOpportunityControls(userId, {
      budgetLiras: budgetLiras!,
      includeSourceA: nextInclude,
    });
  };

  const filteredOpportunities = React.useMemo(() => {
    return filterOpportunities(opportunities ?? [], filterOption);
  }, [opportunities, filterOption]);
  const hasStaleOpportunities = isError && opportunities !== undefined;
  const hasNoOpportunitiesData = isError && opportunities === undefined;

  return (
    <div className="space-y-2">
      {budgetLiras !== undefined && includeSourceA !== undefined && (
        <StickyControlBar
          budgetLiras={budgetLiras}
          onBudgetChange={handleBudgetChange}
          includeSourceA={includeSourceA}
          onIncludeSourceAChange={handleIncludeSourceAChange}
          filterOption={filterOption}
          onFilterChange={setFilterOption}
          selectedTaxonomyFilterCount={selectedTaxonomyFilterCount}
          onOpenTaxonomyFilters={() => setFilterSheetOpen(true)}
          selectedSport={selectedSport}
          onSportChange={setSelectedSport}
        />
      )}

      {hasStaleOpportunities && (
        <div
          role="alert"
          className="rounded-xl border border-amber-800/50 bg-amber-950/30 px-3 py-2 text-[11px] text-amber-300"
        >
          Veriler güncellenemedi; son bilinen fırsatlar gösteriliyor.
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

      {settingsLoading || isLoading ? (
        <OpportunitySkeleton />
      ) : settingsIsError ? (
        <div role="alert" className="rounded-2xl border border-rose-500/30 bg-rose-500/10 p-5 text-center text-xs text-rose-300">
          <p className="font-semibold">Ayarlar yüklenemedi.</p>
          {settingsError instanceof Error && <p className="mt-1">{settingsError.message}</p>}
          <button type="button" onClick={() => refetchSettings()} className="touch-target mt-3 rounded-xl border border-rose-500/30 px-3 font-semibold">
            Tekrar Dene
          </button>
        </div>
      ) : hasNoOpportunitiesData ? (
        <div role="alert" className="rounded-2xl border border-rose-500/30 bg-rose-500/10 p-5 text-center text-xs text-rose-300">
          <p className="font-semibold">Fırsatlar yüklenemedi.</p>
          {error instanceof Error && <p className="mt-1">{error.message}</p>}
          <button type="button" onClick={() => refetch()} className="touch-target mt-3 rounded-xl border border-rose-500/30 px-3 font-semibold">
            Tekrar Dene
          </button>
        </div>
      ) : filteredOpportunities.length > 0 ? (
        <div>
          <div className="text-[11px] font-semibold text-slate-400 mb-2 flex items-center justify-between">
            <span>
              Bulunan Fırsatlar ({filteredOpportunities.length})
            </span>
            {isRefetching && (
              <span className="text-[10px] text-brand-primary animate-pulse font-mono">
                Güncelleniyor...
              </span>
            )}
          </div>
          {filteredOpportunities.map((opp) => (
            <OpportunityCard
              key={opp.opportunity_id}
              opportunity={opp}
              onClick={() => onSelectOpportunity(opp)}
              onSelectAlternative={onSelectOpportunity}
            />
          ))}
        </div>
      ) : (
        <EmptyState
          onGoToAllEvents={onGoToAllEvents}
          onRefresh={() => refreshMutation.mutate()}
          refreshing={refreshMutation.isPending}
          refreshError={
            refreshMutation.isError
              ? refreshMutation.error instanceof Error
                ? refreshMutation.error.message
                : "Veriler yenilenemedi."
              : null
          }
          refreshIntervalSeconds={settings?.system.refresh_interval_seconds}
        />
      )}

      <SafetyDisclaimer />

      <TaxonomyFilterSheet
        open={filterSheetOpen}
        onClose={() => setFilterSheetOpen(false)}
        filters={taxonomyFilters.filters}
        onChange={taxonomyFilters.onChange}
      />
    </div>
  );
};
