import { EventListItem } from "../../types/api";

/** One league/competition bucket of matches inside a country group. Sorted by
 * kickoff ascending; never re-sorted by yield. `positiveCount` is a badge only. */
export interface LeagueEventGroup {
  id: string;
  name: string;
  positiveCount: number;
  events: readonly EventListItem[];
}

/** One country (or International/Unknown/unmapped) bucket of league groups.
 * `isoCode` is non-null only for a verified `country` region - never fabricated
 * for International, Unknown, or the synthetic unmapped bucket. */
export interface CountryEventGroup {
  id: string;
  name: string;
  isoCode: string | null;
  regionClass: "country" | "international" | "unknown" | "unmapped";
  positiveCount: number;
  leagues: readonly LeagueEventGroup[];
}

/** Stable synthetic id/name for events with no verified taxonomy (taxonomy_status
 * "unmapped", country_id/league_id null). Matches the design spec's literal
 * "Eşleşmemiş Ligler" warning group - never a fabricated country or flag. */
export const UNMAPPED_COUNTRY_ID = "unmapped";
export const UNMAPPED_COUNTRY_NAME = "Eşleşmemiş Ligler";

const REGION_CLASS_RANK: Record<CountryEventGroup["regionClass"], number> = {
  international: 0,
  country: 1,
  unknown: 2,
  unmapped: 3,
};

// Must stay consistent with the backend's turkish_sort_key() (surebet.taxonomy.normalizer)
// used by /api/v1/countries and /api/v1/leagues - both order Turkish text the same way.
const collator = new Intl.Collator("tr-TR", { sensitivity: "base" });

function regionClassOf(countryId: string | null): CountryEventGroup["regionClass"] {
  if (countryId === null) return "unmapped";
  if (countryId === "region:international") return "international";
  if (countryId === "region:unknown") return "unknown";
  return "country";
}

/** Turkish-collated name compare with a deterministic id tie-breaker, shared by
 * both the country and league sort passes. */
function compareByNameThenId(a: { name: string; id: string }, b: { name: string; id: string }): number {
  const nameDiff = collator.compare(a.name, b.name);
  if (nameDiff !== 0) return nameDiff;
  if (a.id < b.id) return -1;
  if (a.id > b.id) return 1;
  return 0;
}

function sortEventsByKickoff(events: readonly EventListItem[]): EventListItem[] {
  return [...events].sort((a, b) => {
    const kickoffDiff = Date.parse(a.kickoff) - Date.parse(b.kickoff);
    if (kickoffDiff !== 0) return kickoffDiff;
    if (a.matched_id < b.matched_id) return -1;
    if (a.matched_id > b.matched_id) return 1;
    return 0;
  });
}

/** Builds a regional-indicator flag emoji from a 2-letter ISO 3166-1 alpha-2 code.
 * Returns null for anything that is not exactly 2 letters, so callers can never
 * accidentally render a flag for a fabricated or malformed code. */
export function countryFlagEmoji(isoCode: string | null): string | null {
  if (!isoCode || isoCode.length !== 2 || !/^[a-zA-Z]{2}$/.test(isoCode)) {
    return null;
  }
  const codePoints = [...isoCode.toUpperCase()].map((char) => 127397 + char.charCodeAt(0));
  return String.fromCodePoint(...codePoints);
}

interface MutableLeagueBucket {
  name: string;
  events: EventListItem[];
}

interface MutableCountryBucket {
  name: string;
  isoCode: string | null;
  regionClass: CountryEventGroup["regionClass"];
  leagues: Map<string, MutableLeagueBucket>;
}

/** Groups verified events by country then league, and buckets unmapped events into
 * a single trailing "Eşleşmemiş Ligler" group keyed by their raw source competition
 * text (never a fabricated league name). Order: International first, then countries
 * in Turkish alphabetical order, then Unknown, then the unmapped group last; leagues
 * within a country/region are Turkish-alphabetical; matches within a league are
 * ascending kickoff. IDs break ties at every level for determinism. Only
 * `positiveCount` badges are derived from `is_positive` - nothing is ever re-sorted
 * by yield. */
export function groupEvents(events: readonly EventListItem[]): readonly CountryEventGroup[] {
  const countries = new Map<string, MutableCountryBucket>();

  for (const event of events) {
    const countryId = event.country_id ?? UNMAPPED_COUNTRY_ID;
    let country = countries.get(countryId);
    if (!country) {
      const regionClass = regionClassOf(event.country_id);
      country = {
        name: event.country_id === null ? UNMAPPED_COUNTRY_NAME : event.country_name ?? event.country_id,
        // A flag is only ever shown for a verified `country` region class - International,
        // Unknown, and the unmapped bucket never carry an ISO code, even if upstream data is malformed.
        isoCode: regionClass === "country" ? event.country_iso_code : null,
        regionClass,
        leagues: new Map(),
      };
      countries.set(countryId, country);
    }

    const leagueId = event.league_id ?? `unmapped:${event.competition}`;
    let league = country.leagues.get(leagueId);
    if (!league) {
      league = { name: event.league_name ?? event.competition, events: [] };
      country.leagues.set(leagueId, league);
    }
    league.events.push(event);
  }

  const countryGroups: CountryEventGroup[] = [];
  for (const [countryId, bucket] of countries) {
    const leagueGroups: LeagueEventGroup[] = [];
    for (const [leagueId, leagueBucket] of bucket.leagues) {
      const sortedEvents = sortEventsByKickoff(leagueBucket.events);
      leagueGroups.push({
        id: leagueId,
        name: leagueBucket.name,
        positiveCount: sortedEvents.filter((e) => e.is_positive).length,
        events: sortedEvents,
      });
    }
    leagueGroups.sort(compareByNameThenId);

    countryGroups.push({
      id: countryId,
      name: bucket.name,
      isoCode: bucket.isoCode,
      regionClass: bucket.regionClass,
      positiveCount: leagueGroups.reduce((sum, l) => sum + l.positiveCount, 0),
      leagues: leagueGroups,
    });
  }

  countryGroups.sort((a, b) => {
    const rankDiff = REGION_CLASS_RANK[a.regionClass] - REGION_CLASS_RANK[b.regionClass];
    if (rankDiff !== 0) return rankDiff;
    return compareByNameThenId(a, b);
  });

  return countryGroups;
}
