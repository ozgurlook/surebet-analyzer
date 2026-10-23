import React from "react";
import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import { api } from "../../api/client";
import { TaxonomyFilters } from "../../types/api";

interface TaxonomyFilterSheetProps {
  open: boolean;
  onClose: () => void;
  filters: TaxonomyFilters;
  onChange: (filters: TaxonomyFilters) => void;
}

/** Mobile bottom sheet for multi-country + dependent multi-league selection. Fully
 * controlled (filters/onChange come from the parent page, which owns persistence),
 * so it is reusable as-is by both All Matches and the Opportunities filter UI - each
 * keeps its own separate stored selection. League choices are narrowed server-side
 * by the currently selected countries via the same `getLeagues({ country_ids })`
 * call used by the country/league catalog APIs. */
export const TaxonomyFilterSheet: React.FC<TaxonomyFilterSheetProps> = ({
  open,
  onClose,
  filters,
  onChange,
}) => {
  const {
    data: countries,
    isLoading: countriesLoading,
    isError: countriesError,
    refetch: refetchCountries,
  } = useQuery({
    queryKey: ["countries"],
    queryFn: () => api.getCountries(),
    enabled: open,
  });
  const {
    data: leagues,
    isLoading: leaguesLoading,
    isError: leaguesError,
    refetch: refetchLeagues,
  } = useQuery({
    queryKey: ["leagues", filters.country_ids],
    queryFn: () =>
      api.getLeagues({
        country_ids: filters.country_ids.length > 0 ? filters.country_ids : undefined,
      }),
    enabled: open,
  });

  if (!open) {
    return null;
  }

  const toggleCountry = (countryId: string) => {
    const isSelected = filters.country_ids.includes(countryId);
    const nextCountryIds = isSelected
      ? filters.country_ids.filter((id) => id !== countryId)
      : [...filters.country_ids, countryId];

    // Drop any already-selected league whose country is no longer among the
    // selected countries, so the filter never references an orphaned league.
    const nextLeagueIds =
      nextCountryIds.length === 0
        ? filters.league_ids
        : filters.league_ids.filter((leagueId) =>
            (leagues ?? []).some(
              (league) => league.league_id === leagueId && nextCountryIds.includes(league.country_id)
            )
          );

    onChange({ country_ids: nextCountryIds, league_ids: nextLeagueIds });
  };

  const toggleLeague = (leagueId: string) => {
    const nextLeagueIds = filters.league_ids.includes(leagueId)
      ? filters.league_ids.filter((id) => id !== leagueId)
      : [...filters.league_ids, leagueId];
    onChange({ ...filters, league_ids: nextLeagueIds });
  };

  const clearAll = () => onChange({ country_ids: [], league_ids: [] });
  const optionsLoading = countriesLoading || leaguesLoading;
  const optionsError = countriesError || leaguesError;

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/60"
      role="dialog"
      aria-modal="true"
      aria-label="Ülke ve lig filtrele"
    >
      <div className="w-full max-w-md max-h-[80vh] overflow-y-auto bg-navy-950 border-t border-navy-800 rounded-t-2xl p-4 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-bold text-white">Filtrele</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Kapat"
            className="touch-target flex items-center justify-center text-slate-400"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <button
          type="button"
          onClick={clearAll}
          className="touch-target w-full py-2 rounded-xl bg-navy-900 border border-navy-800 text-xs font-semibold text-slate-300"
        >
          Tümünü Temizle
        </button>

        {optionsLoading ? (
          <div className="rounded-xl border border-navy-800 bg-navy-900 p-4 text-center text-xs text-slate-400 animate-pulse">
            Filtre seçenekleri yükleniyor...
          </div>
        ) : optionsError ? (
          <div role="alert" className="rounded-xl border border-rose-500/30 bg-rose-500/10 p-4 text-center text-xs text-rose-300">
            <p className="font-semibold">Filtre seçenekleri yüklenemedi.</p>
            <button
              type="button"
              onClick={() => {
                refetchCountries();
                refetchLeagues();
              }}
              className="touch-target mt-2 rounded-xl border border-rose-500/30 px-3 font-semibold"
            >
              Tekrar Dene
            </button>
          </div>
        ) : (
          <>
            <section className="space-y-2">
              <h3 className="text-[10px] font-semibold text-slate-400 uppercase tracking-wide">Ülkeler</h3>
              <div className="space-y-1">
                {(countries ?? []).map((country) => (
                  <label
                    key={country.country_id}
                    className="touch-target flex items-center gap-2 px-2 py-2 rounded-lg bg-navy-900 border border-navy-850 text-xs text-slate-200"
                  >
                    <input
                      type="checkbox"
                      checked={filters.country_ids.includes(country.country_id)}
                      onChange={() => toggleCountry(country.country_id)}
                      aria-label={country.display_name}
                    />
                    <span>{country.display_name}</span>
                  </label>
                ))}
              </div>
            </section>

            <section className="space-y-2">
              <h3 className="text-[10px] font-semibold text-slate-400 uppercase tracking-wide">Ligler</h3>
              <div className="space-y-1">
                {(leagues ?? []).map((league) => (
                  <label
                    key={league.league_id}
                    className="touch-target flex items-center gap-2 px-2 py-2 rounded-lg bg-navy-900 border border-navy-850 text-xs text-slate-200"
                  >
                    <input
                      type="checkbox"
                      checked={filters.league_ids.includes(league.league_id)}
                      onChange={() => toggleLeague(league.league_id)}
                      aria-label={league.display_name}
                    />
                    <span>{league.display_name}</span>
                  </label>
                ))}
              </div>
            </section>
          </>
        )}
      </div>
    </div>
  );
};
