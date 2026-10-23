const EXPANDED_EVENT_GROUPS_STORAGE_KEY = "surebet.events.expanded";

/** Parses a session storage key as JSON, discarding (and removing) the key if
 * it holds invalid JSON. Never touches any other stored key. */
function readJson(key: string): unknown {
  const raw = sessionStorage.getItem(key);
  if (raw === null) {
    return undefined;
  }
  try {
    return JSON.parse(raw);
  } catch {
    sessionStorage.removeItem(key);
    return undefined;
  }
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

/** Loads the set of manually expanded country/league group ids for the
 * current session's All Matches accordion. Invalid data falls back to an
 * empty list and removes only its own key. */
export function loadExpandedEventGroups(): readonly string[] {
  const parsed = readJson(EXPANDED_EVENT_GROUPS_STORAGE_KEY);
  if (parsed === undefined) {
    return [];
  }
  if (!isStringArray(parsed)) {
    sessionStorage.removeItem(EXPANDED_EVENT_GROUPS_STORAGE_KEY);
    return [];
  }
  return parsed;
}

/** Persists the set of manually expanded country/league group ids. */
export function saveExpandedEventGroups(ids: readonly string[]): void {
  sessionStorage.setItem(EXPANDED_EVENT_GROUPS_STORAGE_KEY, JSON.stringify(ids));
}

/** Appends one query param per value (`search.append(key, value)` per entry)
 * instead of comma-joining, matching how FastAPI parses
 * `Annotated[list[str] | None, Query()]` on the backend. */
export function appendMany(search: URLSearchParams, key: string, values?: readonly string[]): void {
  values?.forEach((value) => search.append(key, value));
}

/** Serializes a taxonomy filter selection onto a query string as repeated
 * `country_id`/`league_id` params. Shared by the events and opportunities
 * API client methods so both follow the identical repeated-key contract.
 *
 * `use_saved_filters` defaults to `true` on the backend, which treats a
 * fully omitted country_id/league_id as "use my saved filter". A plain
 * query string cannot represent "explicitly cleared" -- an empty array
 * appends no param at all, indistinguishable from "omitted". Passing
 * `use_saved_filters: false` closes that gap: it tells the backend this
 * request carries a full explicit override (show everything when the lists
 * are empty), never to fall back to the saved filter. */
export function appendTaxonomyFilters(
  search: URLSearchParams,
  filters: {
    country_ids?: readonly string[];
    league_ids?: readonly string[];
    use_saved_filters?: boolean;
  }
): void {
  appendMany(search, "country_id", filters.country_ids);
  appendMany(search, "league_id", filters.league_ids);
  if (filters.use_saved_filters === false) {
    search.set("use_saved_filters", "false");
  }
}
