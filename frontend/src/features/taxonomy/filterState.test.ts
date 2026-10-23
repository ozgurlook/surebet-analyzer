import { beforeEach, describe, expect, it } from "vitest";
import {
  appendMany,
  appendTaxonomyFilters,
  loadExpandedEventGroups,
  saveExpandedEventGroups,
} from "./filterState";

const EVENTS_KEY = "surebet.filters.events";
const EXPANDED_KEY = "surebet.events.expanded";

beforeEach(() => {
  sessionStorage.clear();
});

describe("expansion state", () => {
  it("uses the exact session storage key", () => {
    saveExpandedEventGroups(["country:TR", "league:1"]);
    expect(sessionStorage.getItem(EXPANDED_KEY)).toContain("country:TR");
  });

  it("round-trips manually expanded group ids", () => {
    saveExpandedEventGroups(["country:TR", "league:1"]);
    expect(loadExpandedEventGroups()).toEqual(["country:TR", "league:1"]);
  });

  it("returns an empty list when nothing was stored yet", () => {
    expect(loadExpandedEventGroups()).toEqual([]);
  });

  it("falls back to an empty list and removes only the corrupt key on invalid JSON", () => {
    sessionStorage.setItem(EXPANDED_KEY, "not json");
    sessionStorage.setItem(EVENTS_KEY, JSON.stringify({ country_ids: ["country:TR"], league_ids: [] }));

    expect(loadExpandedEventGroups()).toEqual([]);
    expect(sessionStorage.getItem(EXPANDED_KEY)).toBeNull();
    expect(sessionStorage.getItem(EVENTS_KEY)).toContain("country:TR");
  });

  it("falls back to an empty list on wrong-shaped data", () => {
    sessionStorage.setItem(EXPANDED_KEY, JSON.stringify({ not: "an array" }));

    expect(loadExpandedEventGroups()).toEqual([]);
    expect(sessionStorage.getItem(EXPANDED_KEY)).toBeNull();
  });

  it("falls back to an empty list when array contains non-string entries", () => {
    sessionStorage.setItem(EXPANDED_KEY, JSON.stringify(["country:TR", 42]));

    expect(loadExpandedEventGroups()).toEqual([]);
    expect(sessionStorage.getItem(EXPANDED_KEY)).toBeNull();
  });
});

describe("appendMany", () => {
  it("appends each value as a separate query param, not comma-joined", () => {
    const search = new URLSearchParams();
    appendMany(search, "country_id", ["country:TR", "country:DE"]);

    expect(search.getAll("country_id")).toEqual(["country:TR", "country:DE"]);
    expect(search.toString()).toBe("country_id=country%3ATR&country_id=country%3ADE");
    expect(search.toString()).not.toContain(",");
  });

  it("appends nothing when values are undefined or empty", () => {
    const search = new URLSearchParams();
    appendMany(search, "country_id", undefined);
    appendMany(search, "league_id", []);

    expect(search.toString()).toBe("");
  });
});

describe("appendTaxonomyFilters", () => {
  it("serializes country_ids and league_ids as repeated query params", () => {
    const search = new URLSearchParams();
    appendTaxonomyFilters(search, { country_ids: ["country:TR"], league_ids: ["league:1", "league:2"] });

    expect(search.getAll("country_id")).toEqual(["country:TR"]);
    expect(search.getAll("league_id")).toEqual(["league:1", "league:2"]);
    expect(search.toString()).not.toContain(",");
  });

  it("produces an empty query string for empty filters", () => {
    const search = new URLSearchParams();
    appendTaxonomyFilters(search, { country_ids: [], league_ids: [] });

    expect(search.toString()).toBe("");
  });

  it("sends use_saved_filters=false so an explicitly cleared filter list is never read as omitted", () => {
    // An empty array serializes to no query param at all -- indistinguishable
    // from "omitted" on the wire. use_saved_filters=false tells the backend
    // this IS an explicit override (show everything), not "use my saved
    // filters", closing that gap.
    const search = new URLSearchParams();
    appendTaxonomyFilters(search, { country_ids: [], league_ids: [], use_saved_filters: false });

    expect(search.toString()).toBe("use_saved_filters=false");
  });

  it("omits use_saved_filters when true or unset, matching the backend's default", () => {
    const search = new URLSearchParams();
    appendTaxonomyFilters(search, {
      country_ids: ["country:TR"],
      league_ids: [],
      use_saved_filters: true,
    });

    expect(search.toString()).not.toContain("use_saved_filters");
  });
});
