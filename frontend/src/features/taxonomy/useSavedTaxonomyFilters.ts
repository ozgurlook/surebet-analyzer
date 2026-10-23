import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { FilterScope, TaxonomyFilters } from "../../types/api";

const DEBOUNCE_MS = 300;
const EMPTY_FILTERS: TaxonomyFilters = { country_ids: [], league_ids: [] };

// Country/league order is not meaningful (the backend stores both sets as
// unordered id sets), so "does the local selection match what's confirmed on
// the server" must compare as sets, not arrays.
function sameIdSet(a: readonly string[], b: readonly string[]): boolean {
  if (a.length !== b.length) return false;
  const bSet = new Set(b);
  return a.every((id) => bSet.has(id));
}

function filtersEqual(a: TaxonomyFilters, b: TaxonomyFilters): boolean {
  return sameIdSet(a.country_ids, b.country_ids) && sameIdSet(a.league_ids, b.league_ids);
}

function cloneFilters(filters: TaxonomyFilters): TaxonomyFilters {
  return { country_ids: [...filters.country_ids], league_ids: [...filters.league_ids] };
}

// The events/opportunities list queries are namespaced by their own top-level
// query key ("events" / "opportunities" -- see AllEventsPage/OpportunitiesPage),
// which happens to equal the filter scope value.
function listQueryKeyFor(scope: FilterScope): string {
  return scope;
}

export interface TaxonomyQueryOverride {
  country_ids?: readonly string[];
  league_ids?: readonly string[];
  /** `false` only while `country_ids`/`league_ids` above carry an in-flight,
   * unsaved local override -- tells the backend this is a full explicit
   * override (never fall back to the saved filter), which a plain query
   * string cannot otherwise express once the override is an empty list (see
   * `appendTaxonomyFilters`). Omitted (so the backend's own `true` default
   * applies) once `filters` matches the confirmed saved state again. */
  use_saved_filters?: boolean;
}

export interface UseSavedTaxonomyFiltersResult {
  /** Current filter selection. Updated synchronously on every `onChange`
   * call -- never waits on the network. Feed directly to
   * `TaxonomyFilterSheet`'s `filters` prop. */
  filters: TaxonomyFilters;
  /** Feed directly to `TaxonomyFilterSheet`'s `onChange` prop. Updates
   * `filters` immediately and schedules a debounced PUT 300ms later. */
  onChange: (next: TaxonomyFilters) => void;
  /** True while the initial GET for this user+scope is in flight. */
  isLoading: boolean;
  /** True when the most recent debounced (or retried) PUT failed. The
   * optimistic `filters` value is kept as-is -- never silently reverted. */
  isSaveError: boolean;
  saveError: unknown;
  /** Resends the current `filters` selection immediately (no additional
   * debounce delay), clearing `isSaveError` on success. */
  retry: () => void;
  /**
   * Params to merge into the matching events/opportunities GET call: `{}`
   * while `filters` matches the last confirmed server state (so the backend
   * applies its own saved-filter resolution), or explicit id lists while an
   * edit is in flight and not yet persisted.
   */
  queryOverride: TaxonomyQueryOverride;
}

export function useSavedTaxonomyFilters(
  userId: number | null | undefined,
  scope: FilterScope
): UseSavedTaxonomyFiltersResult {
  const queryClient = useQueryClient();
  const enabled = userId != null;
  // Identifies "this user + this scope" so every ref below can be reset
  // exactly when either changes, and so an in-flight request/timer from a
  // just-abandoned key can recognize itself as stale.
  const filterKey = `${userId ?? "none"}:${scope}`;

  const filtersQuery = useQuery({
    queryKey: ["filters", userId, scope],
    queryFn: () => api.getSavedFilters(scope),
    enabled,
  });

  const [filters, setFilters] = useState<TaxonomyFilters>(EMPTY_FILTERS);
  const [isSaveError, setIsSaveError] = useState(false);
  const [saveError, setSaveError] = useState<unknown>(null);

  // The last filter value known for certain to be persisted on the server
  // (from the initial GET, or a successful PUT response). Compared against
  // `filters` to decide whether the caller should send an explicit taxonomy
  // override or let the backend apply its own saved-filter resolution.
  const confirmedRef = useRef<TaxonomyFilters>(EMPTY_FILTERS);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const activeKeyRef = useRef<string>(filterKey);
  const syncedKeyRef = useRef<string | null>(null);

  const clearPendingTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  // Reset all local state when switching user or scope, and cancel any
  // pending debounce timer for the previous key -- a stale write must never
  // land for the wrong user/scope after a switch.
  useEffect(() => {
    activeKeyRef.current = filterKey;
    clearPendingTimer();
    setFilters(EMPTY_FILTERS);
    confirmedRef.current = EMPTY_FILTERS;
    setIsSaveError(false);
    setSaveError(null);
    syncedKeyRef.current = null;
  }, [filterKey, clearPendingTimer]);

  // Cancel any pending timer on unmount.
  useEffect(() => clearPendingTimer, [clearPendingTimer]);

  // Initialize local state from the server response, once per user+scope.
  useEffect(() => {
    if (filtersQuery.data && syncedKeyRef.current !== filterKey) {
      const loaded = cloneFilters(filtersQuery.data);
      confirmedRef.current = loaded;
      setFilters(loaded);
      syncedKeyRef.current = filterKey;
    }
  }, [filtersQuery.data, filterKey]);

  const performSave = useCallback(
    (next: TaxonomyFilters, savingUserId: number, savingScope: FilterScope, savingKey: string) => {
      api
        .saveFilters(savingScope, next)
        .then((saved) => {
          // A user/scope switch since this save was issued makes the
          // response irrelevant to whatever is currently mounted.
          if (activeKeyRef.current !== savingKey) return;
          const normalized = cloneFilters(saved);
          confirmedRef.current = normalized;
          setIsSaveError(false);
          setSaveError(null);
          queryClient.setQueryData(["filters", savingUserId, savingScope], normalized);
          queryClient.invalidateQueries({ queryKey: [listQueryKeyFor(savingScope)] });
        })
        .catch((err) => {
          if (activeKeyRef.current !== savingKey) return;
          setIsSaveError(true);
          setSaveError(err);
        });
    },
    [queryClient]
  );

  const onChange = useCallback(
    (next: TaxonomyFilters) => {
      setFilters(next);
      if (!enabled || userId == null) return;
      const savingKey = filterKey;
      clearPendingTimer();
      timerRef.current = setTimeout(() => {
        timerRef.current = null;
        performSave(next, userId, scope, savingKey);
      }, DEBOUNCE_MS);
    },
    [enabled, userId, scope, filterKey, clearPendingTimer, performSave]
  );

  const retry = useCallback(() => {
    if (!enabled || userId == null) return;
    clearPendingTimer();
    setIsSaveError(false);
    performSave(filters, userId, scope, filterKey);
  }, [enabled, userId, scope, filterKey, filters, clearPendingTimer, performSave]);

  const isOverridden = !filtersEqual(filters, confirmedRef.current);
  const queryOverride: TaxonomyQueryOverride = isOverridden
    ? { country_ids: filters.country_ids, league_ids: filters.league_ids, use_saved_filters: false }
    : {};

  return {
    filters,
    onChange,
    isLoading: enabled && filtersQuery.isLoading,
    isSaveError,
    saveError,
    retry,
    queryOverride,
  };
}
