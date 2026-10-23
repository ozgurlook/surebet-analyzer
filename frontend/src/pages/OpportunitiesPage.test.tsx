import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { OpportunitiesPage } from "./OpportunitiesPage";
import { CalculatedOpportunity, LeagueSummary, RegionSummary } from "../types/api";

function makeOpportunity(overrides: Partial<CalculatedOpportunity> = {}): CalculatedOpportunity {
  return {
    opportunity_id: "test:1",
    matched_id: "match_1",
    competition: "Bundesliga",
    country_id: null,
    country_name: null,
    country_iso_code: null,
    league_id: null,
    league_name: null,
    home: "Bayern",
    away: "Dortmund",
    kickoff: "2026-08-30T18:00:00Z",
    budget_cents: 100000,
    include_source_a_match_sides: false,
    selections: {
      HOME: { outcome: "HOME", source: "source_c", odds: "2.80", stake_cents: 36000, cashback_eligible: true },
      DRAW: { outcome: "DRAW", source: "source_b", odds: "3.60", stake_cents: 29000, cashback_eligible: true },
      AWAY: { outcome: "AWAY", source: "source_c", odds: "3.10", stake_cents: 35000, cashback_eligible: true },
    },
    scenarios: {
      HOME: {
        outcome: "HOME",
        winning_source: "source_c",
        winning_odds: "2.80",
        winning_stake_cents: 36000,
        gross_payout_cents: 100800,
        losing_eligible_stakes_cents: 64000,
        cashback_cents: 6400,
        total_return_cents: 107200,
        total_stake_cents: 100000,
        standard_profit_cents: 800,
        net_profit_cents: 7200,
        net_profit_percent: 7.2,
      },
      DRAW: {
        outcome: "DRAW",
        winning_source: "source_b",
        winning_odds: "3.60",
        winning_stake_cents: 29000,
        gross_payout_cents: 104400,
        losing_eligible_stakes_cents: 71000,
        cashback_cents: 7100,
        total_return_cents: 111500,
        total_stake_cents: 100000,
        standard_profit_cents: 4400,
        net_profit_cents: 11500,
        net_profit_percent: 11.5,
      },
      AWAY: {
        outcome: "AWAY",
        winning_source: "source_c",
        winning_odds: "3.10",
        winning_stake_cents: 35000,
        gross_payout_cents: 108500,
        losing_eligible_stakes_cents: 65000,
        cashback_cents: 6500,
        total_return_cents: 115000,
        total_stake_cents: 100000,
        standard_profit_cents: 8500,
        net_profit_cents: 15000,
        net_profit_percent: 15.0,
      },
    },
    min_adjusted_profit_cents: 7200,
    min_adjusted_profit_percent: 7.2,
    min_standard_profit_cents: 800,
    min_standard_profit_percent: 0.8,
    is_positive: true,
    oldest_quote_age_seconds: 12,
    calculated_at: "2026-08-24T15:00:00Z",
    ...overrides,
  };
}

const countriesFixture: RegionSummary[] = [
  {
    country_id: "country:DE",
    region_class: "country",
    iso_code: "DE",
    display_name: "Almanya",
    league_count: 1,
    event_count: 1,
    opportunity_count: 1,
    freshest_at: null,
  },
  {
    country_id: "country:TR",
    region_class: "country",
    iso_code: "TR",
    display_name: "Türkiye",
    league_count: 0,
    event_count: 0,
    opportunity_count: 0,
    freshest_at: null,
  },
];

const leaguesFixture: LeagueSummary[] = [
  {
    league_id: "league:00000000-0000-4000-8000-000000000001",
    country_id: "country:DE",
    display_name: "Bundesliga",
    kind: "league",
    gender: null,
    age_group: null,
    is_reserve: null,
    active: true,
    event_count: 1,
    opportunity_count: 1,
    freshest_at: null,
  },
];

const settingsFixture = {
  personal: {
    default_budget_cents: 100_000,
    include_source_a_match_sides: false,
    tracked_competitions_only: true,
    updated_at: "2026-08-31T12:00:00Z",
  },
  system: {
    refresh_interval_seconds: 40,
    staleness_limit_seconds: 120,
    updated_at: "2026-08-31T12:00:00Z",
  },
};

const EMPTY_FILTERS = { country_ids: [], league_ids: [] };

const TEST_USER_ID = 1;

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return {
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <OpportunitiesPage userId={TEST_USER_ID} onSelectOpportunity={vi.fn()} onGoToAllEvents={vi.fn()} />
      </QueryClientProvider>
    ),
  };
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  vi.spyOn(api, "getCountries").mockResolvedValue(countriesFixture);
  vi.spyOn(api, "getLeagues").mockResolvedValue(leaguesFixture);
  vi.spyOn(api, "getSettings").mockResolvedValue(settingsFixture);
  vi.spyOn(api, "getSavedFilters").mockResolvedValue(EMPTY_FILTERS);
  // Never resolves within a test's synchronous/awaited assertions, so the
  // hook's "confirmed" server state never silently advances mid-test -
  // whether a save actually lands is exercised separately, with fake timers,
  // in the "saved filter persistence" describe block below.
  vi.spyOn(api, "saveFilters").mockImplementation(() => new Promise(() => {}));
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("OpportunitiesPage taxonomy filters", () => {
  it("opens the filter sheet, applies a country+league selection, shows the selected count, and requests filtered opportunities", async () => {
    const spy = vi.spyOn(api, "getOpportunities").mockResolvedValue([]);
    renderPage();
    await waitFor(() => expect(spy).toHaveBeenCalled());

    fireEvent.click(await screen.findByRole("button", { name: /Ülke\/Lig/ }));
    const countryCheckbox = await screen.findByRole("checkbox", { name: /Almanya/ });
    fireEvent.click(countryCheckbox);

    const leagueCheckbox = await screen.findByRole("checkbox", { name: /Bundesliga/ });
    fireEvent.click(leagueCheckbox);

    await waitFor(() =>
      expect(spy.mock.calls[spy.mock.calls.length - 1][0]).toMatchObject({
        country_ids: ["country:DE"],
        league_ids: ["league:00000000-0000-4000-8000-000000000001"],
      })
    );
    expect(screen.getByText("2")).toBeInTheDocument();

    // Selecting a second country must add to, not replace, the selection -
    // multiple ids reach the API as a repeated-param array.
    const secondCountryCheckbox = await screen.findByRole("checkbox", { name: /Türkiye/ });
    fireEvent.click(secondCountryCheckbox);
    await waitFor(() =>
      expect(spy.mock.calls[spy.mock.calls.length - 1][0]).toMatchObject({
        country_ids: ["country:DE", "country:TR"],
        league_ids: ["league:00000000-0000-4000-8000-000000000001"],
      })
    );
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("narrows league choices to the selected country via getLeagues(country_ids)", async () => {
    vi.spyOn(api, "getOpportunities").mockResolvedValue([]);
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: /Ülke\/Lig/ }));
    const countryCheckbox = await screen.findByRole("checkbox", { name: /Almanya/ });
    fireEvent.click(countryCheckbox);

    await waitFor(() =>
      expect(api.getLeagues).toHaveBeenLastCalledWith(
        expect.objectContaining({ country_ids: ["country:DE"] })
      )
    );
  });

  it("clears all filters via the Clear All action", async () => {
    const spy = vi.spyOn(api, "getOpportunities").mockResolvedValue([]);
    renderPage();
    await waitFor(() => expect(spy).toHaveBeenCalled());

    fireEvent.click(await screen.findByRole("button", { name: /Ülke\/Lig/ }));
    fireEvent.click(await screen.findByRole("checkbox", { name: /Almanya/ }));
    await waitFor(() =>
      expect(spy.mock.calls[spy.mock.calls.length - 1][0]).toMatchObject({
        country_ids: ["country:DE"],
      })
    );

    fireEvent.click(screen.getByRole("button", { name: /Tümünü Temizle/ }));

    // Clearing back to the (still-unsaved, still-empty) confirmed server state
    // means the request omits country_id/league_id entirely -- letting the
    // backend apply its own saved-filter resolution -- rather than sending
    // explicit empty lists.
    await waitFor(() => {
      const lastCall = spy.mock.calls[spy.mock.calls.length - 1][0];
      expect(lastCall).not.toHaveProperty("country_ids");
      expect(lastCall).not.toHaveProperty("league_ids");
    });
    expect(screen.queryByText("1")).not.toBeInTheDocument();
  });

  it("omits country/league query params on first load, letting the backend apply the saved default", async () => {
    const spy = vi.spyOn(api, "getOpportunities").mockResolvedValue([]);
    renderPage();

    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy.mock.calls[0][0]).not.toHaveProperty("country_ids");
    expect(spy.mock.calls[0][0]).not.toHaveProperty("league_ids");
  });

  it("loads and saves filters scoped to the opportunities preference key, independent of the events scope", async () => {
    const opportunitiesFilters = { country_ids: ["country:TR"], league_ids: [] };
    const eventsFilters = { country_ids: ["country:DE"], league_ids: ["league:xyz"] };
    vi.mocked(api.getSavedFilters).mockImplementation(async (scope) =>
      scope === "opportunities" ? opportunitiesFilters : eventsFilters
    );
    const saveSpy = vi.mocked(api.saveFilters).mockImplementation(async (_scope, filters) => filters);
    vi.spyOn(api, "getOpportunities").mockResolvedValue([]);
    renderPage();

    // Seeded from the opportunities-scoped saved value only, never the events one.
    await waitFor(() => expect(screen.getByText("1")).toBeInTheDocument());
    expect(api.getSavedFilters).toHaveBeenCalledWith("opportunities");
    expect(api.getSavedFilters).not.toHaveBeenCalledWith("events");

    fireEvent.click(await screen.findByRole("button", { name: /Ülke\/Lig/ }));
    fireEvent.click(await screen.findByRole("checkbox", { name: /Almanya/ }));

    await waitFor(() => expect(saveSpy).toHaveBeenCalled(), { timeout: 1000 });
    // The debounced write persists to the opportunities scope, never events.
    expect(saveSpy.mock.calls.every(([scope]) => scope === "opportunities")).toBe(true);
  });

  it("scopes the saved filter selection to the authenticated user, refetching fresh state on a user switch", async () => {
    // Deliberately different selection *counts* (1 vs 2) so the badge text
    // itself proves which user's data is showing, not just that some count renders.
    const userOneFilters = { country_ids: ["country:DE"], league_ids: [] };
    const userTwoFilters = { country_ids: ["country:TR"], league_ids: ["league:extra"] };
    const getSavedFiltersMock = vi.mocked(api.getSavedFilters).mockResolvedValue(userOneFilters);
    vi.spyOn(api, "getOpportunities").mockResolvedValue([]);

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const { rerender } = render(
      <QueryClientProvider client={queryClient}>
        <OpportunitiesPage userId={1} onSelectOpportunity={vi.fn()} onGoToAllEvents={vi.fn()} />
      </QueryClientProvider>
    );

    await waitFor(() => expect(screen.getByText("1")).toBeInTheDocument());
    expect(getSavedFiltersMock).toHaveBeenCalledTimes(1);

    getSavedFiltersMock.mockResolvedValue(userTwoFilters);
    rerender(
      <QueryClientProvider client={queryClient}>
        <OpportunitiesPage userId={2} onSelectOpportunity={vi.fn()} onGoToAllEvents={vi.fn()} />
      </QueryClientProvider>
    );

    // User 1's selection count ("1") must never flash while user 2's state
    // loads: it should go straight to user 2's own count ("2"), or a
    // neutral/absent state in between -- never linger on the stale "1".
    expect(screen.queryByText("1")).not.toBeInTheDocument();
    await waitFor(() => expect(getSavedFiltersMock).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByText("2")).toBeInTheDocument());
  });

  it("shows a retryable error banner (without discarding the selection) when a filter save fails", async () => {
    vi.spyOn(api, "getOpportunities").mockResolvedValue([]);
    const saveSpy = vi
      .mocked(api.saveFilters)
      .mockRejectedValueOnce(new Error("network down"))
      .mockResolvedValueOnce({ country_ids: ["country:DE"], league_ids: [] });
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: /Ülke\/Lig/ }));
    fireEvent.click(await screen.findByRole("checkbox", { name: /Almanya/ }));

    const retryButton = await screen.findByRole("button", { name: "Tekrar Dene" });
    expect(screen.getByText("Filtre tercihleri kaydedilemedi.")).toBeInTheDocument();
    // The selection itself is not silently reverted.
    expect(screen.getByText("1")).toBeInTheDocument();

    fireEvent.click(retryButton);
    await waitFor(() => expect(saveSpy).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(screen.queryByText("Filtre tercihleri kaydedilemedi.")).not.toBeInTheDocument()
    );
  });
});

describe("OpportunitiesPage ordering", () => {
  // These fixtures encode "today" as 2026-08-30 and "tomorrow" as 2026-08-31, so the
  // clock must be pinned; against the real clock this test starts failing on 2026-08-31.
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date("2026-08-30T12:00:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("never reorders remaining opportunities after a quick filter removes an item (server profit order stays authoritative)", async () => {
    // Deliberately not chronological, matching how the real API sorts by profit
    // descending regardless of kickoff time - a naive "today" filter that resorted
    // by kickoff would put oppMid before oppHigh, which must never happen.
    const oppHigh = makeOpportunity({
      opportunity_id: "high",
      home: "HighMargin",
      away: "TeamA",
      kickoff: "2026-08-30T20:00:00Z",
      min_adjusted_profit_percent: 10,
    });
    const oppMid = makeOpportunity({
      opportunity_id: "mid",
      home: "MidMargin",
      away: "TeamB",
      kickoff: "2026-08-30T10:00:00Z",
      min_adjusted_profit_percent: 5,
    });
    const oppLow = makeOpportunity({
      opportunity_id: "low",
      home: "LowMargin",
      away: "TeamC",
      kickoff: "2026-08-31T10:00:00Z", // tomorrow: excluded by "today" filter
      min_adjusted_profit_percent: 1,
    });

    vi.spyOn(api, "getOpportunities").mockResolvedValue([oppHigh, oppMid, oppLow]);
    renderPage();

    await screen.findByText(/HighMargin/);
    expect(screen.getByText(/LowMargin/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Bugün" }));

    await waitFor(() => expect(screen.queryByText(/LowMargin/)).not.toBeInTheDocument());
    expect(screen.getByText(/HighMargin/)).toBeInTheDocument();
    expect(screen.getByText(/MidMargin/)).toBeInTheDocument();

    const highEl = screen.getByText(/HighMargin/);
    const midEl = screen.getByText(/MidMargin/);
    // HighMargin (server-first, higher profit) must still precede MidMargin in the DOM,
    // even though MidMargin's kickoff time is earlier in the day.
    expect(highEl.compareDocumentPosition(midEl) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

describe("OpportunitiesPage existing quick filters", () => {
  it("starts from the saved budget and Kaynak A-side preference", async () => {
    vi.mocked(api.getSettings).mockResolvedValue({
      personal: {
        default_budget_cents: 500_000,
        include_source_a_match_sides: true,
        tracked_competitions_only: true,
        updated_at: "2026-08-31T12:00:00Z",
      },
      system: {
        refresh_interval_seconds: 40,
        staleness_limit_seconds: 120,
        updated_at: "2026-08-31T12:00:00Z",
      },
    });
    vi.spyOn(api, "getOpportunities").mockResolvedValue([]);

    renderPage();

    expect(await screen.findByText("5.000")).toBeInTheDocument();
    expect(screen.getByLabelText("Kaynak A yan oran switch")).toHaveTextContent("Açık");
  });

  it("still forwards budget and Kaynak A toggle changes to the API unchanged", async () => {
    const spy = vi.spyOn(api, "getOpportunities").mockResolvedValue([]);
    renderPage();
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy.mock.calls[0][0]).toMatchObject({
      budget_cents: 100000,
      include_source_a_match_sides: false,
    });

    fireEvent.click(screen.getByText("2.5k"));
    await waitFor(() =>
      expect(spy.mock.calls[spy.mock.calls.length - 1][0]).toMatchObject({ budget_cents: 250000 })
    );

    fireEvent.click(screen.getByLabelText("Kaynak A yan oran switch"));
    await waitFor(() =>
      expect(spy.mock.calls[spy.mock.calls.length - 1][0]).toMatchObject({
        include_source_a_match_sides: true,
      })
    );
  });

  it("keeps budget and Kaynak A overrides when the page is remounted", async () => {
    vi.spyOn(api, "getOpportunities").mockResolvedValue([]);
    const firstRender = renderPage();

    await screen.findByText("1.000");
    fireEvent.click(screen.getByText("2.5k"));
    fireEvent.click(screen.getByLabelText("Kaynak A yan oran switch"));
    await waitFor(() =>
      expect(screen.getByLabelText("Kaynak A yan oran switch")).toHaveTextContent("Açık")
    );

    firstRender.unmount();
    renderPage();

    expect(await screen.findByText("2.500")).toBeInTheDocument();
    expect(screen.getByLabelText("Kaynak A yan oran switch")).toHaveTextContent("Açık");
  });

  it("uses the Europe/Istanbul calendar day for the Today filter", async () => {
    vi.setSystemTime(new Date("2026-08-30T21:30:00Z")); // 31 Aug, 00:30 in Istanbul
    const istanbulToday = makeOpportunity({
      opportunity_id: "istanbul-today",
      home: "IstanbulToday",
      kickoff: "2026-08-30T22:00:00Z", // 31 Aug, 01:00 in Istanbul
    });
    const previousIstanbulDay = makeOpportunity({
      opportunity_id: "previous-istanbul-day",
      home: "PreviousIstanbulDay",
      kickoff: "2026-08-30T10:00:00Z", // 30 Aug in Istanbul
    });
    const istanbulTomorrow = makeOpportunity({
      opportunity_id: "istanbul-tomorrow",
      home: "IstanbulTomorrow",
      kickoff: "2026-08-31T22:00:00Z", // 1 Sep in Istanbul
    });
    vi.spyOn(api, "getOpportunities").mockResolvedValue([
      previousIstanbulDay,
      istanbulToday,
      istanbulTomorrow,
    ]);
    renderPage();

    await screen.findByText(/IstanbulToday/);
    fireEvent.click(screen.getByRole("button", { name: "Bugün" }));

    expect(screen.getByText(/IstanbulToday/)).toBeInTheDocument();
    expect(screen.queryByText(/PreviousIstanbulDay/)).not.toBeInTheDocument();
    expect(screen.queryByText(/IstanbulTomorrow/)).not.toBeInTheDocument();
  });

  it("shows only dates after Istanbul today in the Upcoming filter", async () => {
    vi.setSystemTime(new Date("2026-08-30T21:30:00Z"));
    vi.spyOn(api, "getOpportunities").mockResolvedValue([
      makeOpportunity({
        opportunity_id: "today",
        home: "TodayTeam",
        kickoff: "2026-08-30T22:00:00Z",
      }),
      makeOpportunity({
        opportunity_id: "future",
        home: "FutureTeam",
        kickoff: "2026-08-31T22:00:00Z",
      }),
    ]);
    renderPage();

    await screen.findByText(/TodayTeam/);
    fireEvent.click(screen.getByRole("button", { name: "Yaklaşan" }));

    expect(screen.queryByText(/TodayTeam/)).not.toBeInTheDocument();
    expect(screen.getByText(/FutureTeam/)).toBeInTheDocument();
  });

  it("shows a retryable error instead of an empty state when opportunities cannot load", async () => {
    const getOpportunities = vi
      .spyOn(api, "getOpportunities")
      .mockRejectedValue(new Error("network down"));
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("Fırsatlar yüklenemedi");
    expect(screen.queryByText(/Pozitif Fırsat Yok/)).not.toBeInTheDocument();

    getOpportunities.mockResolvedValue([]);
    fireEvent.click(screen.getByRole("button", { name: "Tekrar Dene" }));
    expect(await screen.findByText(/Pozitif Fırsat Yok/)).toBeInTheDocument();
  });

  it("keeps prior opportunities visible when a background refresh fails", async () => {
    vi.spyOn(api, "getOpportunities").mockResolvedValueOnce([makeOpportunity()]);
    const { queryClient } = renderPage();
    await screen.findByText(/Bayern/);

    vi.mocked(api.getOpportunities).mockRejectedValueOnce(new Error("network down"));
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: ["opportunities"] });
    });

    expect(await screen.findByRole("alert")).toHaveTextContent("son bilinen fırsatlar");
    expect(screen.getByText(/Bayern/)).toBeInTheDocument();
  });

  it("starts a real source collection from the empty state and stays pending", async () => {
    vi.spyOn(api, "getOpportunities").mockResolvedValue([]);
    let finishRefresh!: (value: { status: string }) => void;
    const refreshSources = vi
      .spyOn(api, "refreshSources")
      .mockImplementation(() => new Promise((resolve) => { finishRefresh = resolve; }));
    renderPage();

    expect(await screen.findByText(/40 saniyede bir/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Verileri Yeniden Tara" }));

    await waitFor(() => expect(refreshSources).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("button", { name: "Veriler taranıyor..." })).toBeDisabled();
    await act(async () => finishRefresh({ status: "triggered" }));
  });
});
