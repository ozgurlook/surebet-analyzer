import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { LeagueSummary, UnmatchedItem } from "../../types/api";
import { MappingReviewSheet } from "./MappingReviewSheet";

const item: UnmatchedItem = {
  source: "source_b",
  competition_id: "comp-2",
  identity_kind: "derived_text",
  competition_name: "Bilinmeyen Turnuva",
  country_id: null,
  country_name: null,
  country_alias: "Almanya",
  season: "2026",
  kind: "league",
  gender: null,
  age_group: null,
  is_reserve: null,
  first_seen_at: "2026-08-20T09:00:00Z",
  last_seen_at: "2026-08-29T09:00:00Z",
  updated_at: "2026-08-29T09:00:00Z",
  method: null,
  confidence: null,
  review_state: null,
  target: null,
  conflict_reasons: ["derived_text_identity", "unresolved_country"],
};

function makeLeague(index: number, display_name = `League ${index.toString().padStart(4, "0")}`): LeagueSummary {
  return {
    league_id: `league:${index}`,
    country_id: "country:DE",
    display_name,
    kind: "league",
    gender: null,
    age_group: null,
    is_reserve: null,
    active: true,
    event_count: 0,
    opportunity_count: 0,
    freshest_at: null,
  };
}

// Three pages: 200 + 200 + 47, matching the backend's page-limit cap
// (_MAX_LEAGUES_LIMIT = 200 in src/surebet/web/routes/taxonomy.py). The
// distinguishing league only exists on the third page.
const PAGE_ONE = Array.from({ length: 200 }, (_, i) => makeLeague(i));
const PAGE_TWO = Array.from({ length: 200 }, (_, i) => makeLeague(200 + i));
const THIRD_PAGE_LEAGUE = makeLeague(446, "Zzyzx Rovers");
const PAGE_THREE = [...Array.from({ length: 46 }, (_, i) => makeLeague(400 + i)), THIRD_PAGE_LEAGUE];

function renderSheet() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MappingReviewSheet item={item} onClose={vi.fn()} onMutated={vi.fn()} onConflict={vi.fn()} />
    </QueryClientProvider>
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("MappingReviewSheet link picker", () => {
  it("pages through the full league list so leagues beyond the first 200 are searchable", async () => {
    const getLeaguesSpy = vi.spyOn(api, "getLeagues").mockImplementation(async (params) => {
      const offset = params?.offset ?? 0;
      if (offset === 0) return PAGE_ONE;
      if (offset === 200) return PAGE_TWO;
      if (offset === 400) return PAGE_THREE;
      return [];
    });

    renderSheet();

    fireEvent.click(await screen.findByRole("button", { name: "Başka Lige Bağla" }));

    // Wait until all three pages have been fetched (the third, short page
    // is the signal that paging has stopped correctly).
    await waitFor(() => {
      expect(getLeaguesSpy).toHaveBeenCalledWith({
        full_catalog: true,
        limit: 200,
        offset: 400,
      });
    });

    const searchBox = screen.getByPlaceholderText("Lig ara...");
    fireEvent.change(searchBox, { target: { value: "Zzyzx" } });

    const leagueButton = await screen.findByRole("button", { name: "Zzyzx Rovers" });
    fireEvent.click(leagueButton);
    expect(leagueButton).toHaveAttribute("aria-pressed", "true");

    // Only 3 requests should have been made (200 + 200 + 47), not more.
    expect(getLeaguesSpy).toHaveBeenCalledTimes(3);
    expect(getLeaguesSpy).toHaveBeenNthCalledWith(1, {
      full_catalog: true,
      limit: 200,
      offset: 0,
    });
  });
});

describe("MappingReviewSheet create form", () => {
  it("requests the explicit full country catalog for a new manual mapping", async () => {
    const getCountriesSpy = vi.spyOn(api, "getCountries").mockResolvedValue([]);

    renderSheet();

    fireEvent.click(await screen.findByRole("button", { name: "Yeni Lig Oluştur" }));

    await waitFor(() => {
      expect(getCountriesSpy).toHaveBeenCalledWith({ full_catalog: true });
    });
  });
});
