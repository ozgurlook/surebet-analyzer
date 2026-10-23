import { describe, expect, it } from "vitest";
import { countryFlagEmoji, groupEvents } from "./groupEvents";
import { EventListItem } from "../../types/api";

let counter = 0;

function makeEvent(overrides: Partial<EventListItem> = {}): EventListItem {
  counter += 1;
  return {
    matched_id: `match_${counter}`,
    taxonomy_status: "verified",
    competition: "Test Competition",
    country_id: "country:DE",
    country_name: "Almanya",
    country_iso_code: "DE",
    league_id: "league:00000000-0000-4000-8000-000000000001",
    league_name: "Bundesliga",
    home: "Home Team",
    away: "Away Team",
    kickoff: "2026-08-30T18:00:00Z",
    sources: ["source_c"],
    best_margin_percent: null,
    is_positive: false,
    quotes: {},
    ...overrides,
  };
}

describe("groupEvents", () => {
  it("puts the international group first, then countries in Turkish alphabetical order, with the unmapped group last", () => {
    // Turkish collation places C < Ç < D, but a naive JS default sort (codepoint compare)
    // would place Çekya (0x00C7) AFTER Danimarka (starts with 'D' = 0x44) since 0xC7 > 0x44.
    const danimarka = makeEvent({
      matched_id: "dk-1",
      country_id: "country:DK",
      country_name: "Danimarka",
      country_iso_code: "DK",
      league_id: "league:00000000-0000-4000-8000-0000000000d1",
      league_name: "Superliga",
    });
    const cekya = makeEvent({
      matched_id: "cz-1",
      country_id: "country:CZ",
      country_name: "Çekya",
      country_iso_code: "CZ",
      league_id: "league:00000000-0000-4000-8000-0000000000c1",
      league_name: "Fortuna Liga",
    });
    const international = makeEvent({
      matched_id: "intl-1",
      country_id: "region:international",
      country_name: "Uluslararası",
      country_iso_code: null,
      league_id: "league:00000000-0000-4000-8000-000000000i1",
      league_name: "Champions League",
    });
    const unmapped = makeEvent({
      matched_id: "unmapped-1",
      taxonomy_status: "unmapped",
      country_id: null,
      country_name: null,
      country_iso_code: null,
      league_id: null,
      league_name: null,
      competition: "Bilinmeyen Lig",
    });

    const groups = groupEvents([danimarka, cekya, international, unmapped]);

    expect(groups.map((g) => g.id)).toEqual([
      "region:international",
      "country:CZ",
      "country:DK",
      "unmapped",
    ]);
    expect(groups.map((g) => g.regionClass)).toEqual([
      "international",
      "country",
      "country",
      "unmapped",
    ]);
  });

  it("sorts leagues alphabetically in Turkish order within a country and breaks ties on id", () => {
    const dLeague = makeEvent({
      matched_id: "d-1",
      league_id: "league:00000000-0000-4000-8000-00000000000d",
      league_name: "Dost Ligi",
    });
    const cLeague = makeEvent({
      matched_id: "c-1",
      league_id: "league:00000000-0000-4000-8000-00000000000c",
      league_name: "Çay Ligi",
    });
    const tieB = makeEvent({
      matched_id: "tie-b",
      league_id: "league:b",
      league_name: "Süper Lig",
    });
    const tieA = makeEvent({
      matched_id: "tie-a",
      league_id: "league:a",
      league_name: "Süper Lig",
    });

    const groups = groupEvents([dLeague, cLeague, tieB, tieA]);
    expect(groups).toHaveLength(1);
    // Turkish alphabet order is ...C Ç D E...S Ş T..., so Çay Ligi < Dost Ligi < Süper Lig.
    expect(groups[0].leagues.map((l) => l.id)).toEqual([
      "league:00000000-0000-4000-8000-00000000000c",
      "league:00000000-0000-4000-8000-00000000000d",
      "league:a",
      "league:b",
    ]);
  });

  it("orders matches within a league by ascending kickoff, breaking ties on matched_id", () => {
    const later = makeEvent({ matched_id: "b", kickoff: "2026-08-30T20:00:00Z" });
    const earlier = makeEvent({ matched_id: "a", kickoff: "2026-08-30T14:00:00Z" });
    const sameTimeB = makeEvent({ matched_id: "same-b", kickoff: "2026-08-30T14:00:00Z" });
    const sameTimeA = makeEvent({ matched_id: "same-a", kickoff: "2026-08-30T14:00:00Z" });

    const groups = groupEvents([later, earlier, sameTimeB, sameTimeA]);
    expect(groups[0].leagues[0].events.map((e) => e.matched_id)).toEqual([
      "a",
      "same-a",
      "same-b",
      "b",
    ]);
  });

  it("groups unmapped events into a single trailing 'Eşleşmemiş Ligler' group with raw competition text as the league label and no flag", () => {
    const unmappedA = makeEvent({
      matched_id: "u-a",
      taxonomy_status: "unmapped",
      country_id: null,
      country_name: null,
      country_iso_code: "DE", // defensively populated by a buggy backend; must never leak into the group
      league_id: null,
      league_name: null,
      competition: "Regional Cup A",
    });
    const unmappedB = makeEvent({
      matched_id: "u-b",
      taxonomy_status: "unmapped",
      country_id: null,
      country_name: null,
      country_iso_code: null,
      league_id: null,
      league_name: null,
      competition: "Regional Cup B",
    });
    const verified = makeEvent({ matched_id: "v-1" });

    const groups = groupEvents([unmappedA, unmappedB, verified]);

    expect(groups[groups.length - 1]).toMatchObject({
      id: "unmapped",
      name: "Eşleşmemiş Ligler",
      isoCode: null,
      regionClass: "unmapped",
    });
    const unmappedGroup = groups[groups.length - 1];
    expect(unmappedGroup.leagues.map((l) => l.name).sort()).toEqual([
      "Regional Cup A",
      "Regional Cup B",
    ]);
    // Never fabricates a flag for the unmapped bucket, even if source data was polluted.
    expect(unmappedGroup.isoCode).toBeNull();
  });

  it("computes positiveCount badges from is_positive events without reordering by yield", () => {
    const alphaFirstNoPositive = makeEvent({
      matched_id: "alpha-1",
      country_id: "country:AT",
      country_name: "Avusturya",
      country_iso_code: "AT",
      league_id: "league:00000000-0000-4000-8000-0000000000a1",
      league_name: "Bundesliga AT",
      is_positive: false,
      best_margin_percent: null,
    });
    const laterHighMargin = makeEvent({
      matched_id: "beta-1",
      country_id: "country:BE",
      country_name: "Belçika",
      country_iso_code: "BE",
      league_id: "league:00000000-0000-4000-8000-0000000000b1",
      league_name: "Jupiler Pro League",
      is_positive: true,
      best_margin_percent: 12.5,
    });
    const secondPositiveInBelgium = makeEvent({
      matched_id: "beta-2",
      country_id: "country:BE",
      country_name: "Belçika",
      country_iso_code: "BE",
      league_id: "league:00000000-0000-4000-8000-0000000000b1",
      league_name: "Jupiler Pro League",
      is_positive: true,
      best_margin_percent: 3.0,
      kickoff: "2026-08-30T19:00:00Z",
    });

    const groups = groupEvents([laterHighMargin, secondPositiveInBelgium, alphaFirstNoPositive]);

    // Order stays alphabetical (Avusturya before Belçika) despite Belgium having the positive yield.
    expect(groups.map((g) => g.id)).toEqual(["country:AT", "country:BE"]);
    expect(groups[0].positiveCount).toBe(0);
    expect(groups[1].positiveCount).toBe(2);
    expect(groups[1].leagues[0].positiveCount).toBe(2);
  });

  it("assigns region_class 'unknown' its own bucket ranked between countries and the unmapped group, never inventing a flag", () => {
    const unknownRegion = makeEvent({
      matched_id: "unk-1",
      country_id: "region:unknown",
      country_name: "Bilinmeyen Bölge",
      country_iso_code: "ZZ", // defensive: backend must never send this, but the frontend must not display it either
      league_id: "league:00000000-0000-4000-8000-000000000u1",
      league_name: "Unclassified League",
    });
    const country = makeEvent({ matched_id: "c-1" });
    const unmapped = makeEvent({
      matched_id: "um-1",
      taxonomy_status: "unmapped",
      country_id: null,
      country_name: null,
      country_iso_code: null,
      league_id: null,
      league_name: null,
    });

    const groups = groupEvents([unmapped, country, unknownRegion]);

    expect(groups.map((g) => g.regionClass)).toEqual(["country", "unknown", "unmapped"]);
    const unknownGroup = groups.find((g) => g.regionClass === "unknown");
    expect(unknownGroup?.isoCode).toBeNull();
  });

  it("returns an empty list for no events", () => {
    expect(groupEvents([])).toEqual([]);
  });
});

describe("countryFlagEmoji", () => {
  it("builds a regional indicator flag emoji from a 2-letter ISO code", () => {
    expect(countryFlagEmoji("DE")).toBe("\u{1F1E9}\u{1F1EA}");
    expect(countryFlagEmoji("tr")).toBe("\u{1F1F9}\u{1F1F7}");
  });

  it("returns null for a missing or malformed code", () => {
    expect(countryFlagEmoji(null)).toBeNull();
    expect(countryFlagEmoji("")).toBeNull();
    expect(countryFlagEmoji("XYZ")).toBeNull();
  });
});
