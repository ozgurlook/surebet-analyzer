import React from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { AllEventsPage } from "./AllEventsPage";
import { EventListItem, LeagueSummary, RegionSummary } from "../types/api";

function makeEvent(overrides: Partial<EventListItem> = {}): EventListItem {
  return {
    matched_id: "match_1",
    taxonomy_status: "verified",
    competition: "Bundesliga",
    country_id: "country:DE",
    country_name: "Almanya",
    country_iso_code: "DE",
    league_id: "league:00000000-0000-4000-8000-000000000001",
    league_name: "Bundesliga",
    home: "Bayern",
    away: "Dortmund",
    kickoff: "2026-08-30T18:00:00Z",
    sources: ["source_c", "source_b", "source_a"],
    best_margin_percent: null,
    is_positive: false,
    quotes: {
      "source_c:HOME": "1.90",
      "source_c:DRAW": "3.40",
      "source_c:AWAY": "4.10",
      "source_b:HOME": "1.95",
      "source_b:DRAW": "3.30",
      "source_b:AWAY": "4.00",
    },
    ...overrides,
  };
}

const positiveGermanEvent = makeEvent({
  matched_id: "de-1",
  is_positive: true,
  best_margin_percent: 5.5,
});

const ordinaryTurkishEvent = makeEvent({
  matched_id: "tr-1",
  country_id: "country:TR",
  country_name: "Türkiye",
  country_iso_code: "TR",
  league_id: "league:00000000-0000-4000-8000-000000000002",
  league_name: "Süper Lig",
  home: "Galatasaray",
  away: "Fenerbahçe",
  is_positive: false,
  best_margin_percent: null,
});

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
    league_count: 1,
    event_count: 1,
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

const TEST_USER_ID = 1;
const EMPTY_FILTERS = { country_ids: [], league_ids: [] };

function renderPage(userId: number = TEST_USER_ID) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return {
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <AllEventsPage userId={userId} />
      </QueryClientProvider>
    ),
  };
}

beforeEach(() => {
  sessionStorage.clear();
  vi.spyOn(api, "getCountries").mockResolvedValue(countriesFixture);
  vi.spyOn(api, "getLeagues").mockResolvedValue(leaguesFixture);
  vi.spyOn(api, "getSavedFilters").mockResolvedValue(EMPTY_FILTERS);
  // Never resolves within a test's synchronous/awaited assertions, so the
  // hook's "confirmed" server state never silently advances mid-test - save
  // mechanics themselves are exercised separately below.
  vi.spyOn(api, "saveFilters").mockImplementation(() => new Promise(() => {}));
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("AllEventsPage", () => {
  it("defaults to the today time scope on the first request", async () => {
    const spy = vi.spyOn(api, "getEvents").mockResolvedValue([]);
    renderPage();

    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy.mock.calls[0][0]).toMatchObject({ time_scope: "today" });
  });

  it("re-requests events when Yarın/Tümü time scope chips are selected", async () => {
    const spy = vi.spyOn(api, "getEvents").mockResolvedValue([]);
    renderPage();
    await waitFor(() => expect(spy).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("button", { name: "Yarın" }));
    await waitFor(() =>
      expect(spy.mock.calls[spy.mock.calls.length - 1][0]).toMatchObject({ time_scope: "tomorrow" })
    );

    fireEvent.click(screen.getByRole("button", { name: "Tümü" }));
    await waitFor(() =>
      expect(spy.mock.calls[spy.mock.calls.length - 1][0]).toMatchObject({ time_scope: "all" })
    );
  });

  it("re-requests events when sport filter chips are selected", async () => {
    const spy = vi.spyOn(api, "getEvents").mockResolvedValue([]);
    renderPage();
    await waitFor(() => expect(spy).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("button", { name: "⚽ Futbol" }));
    await waitFor(() =>
      expect(spy.mock.calls[spy.mock.calls.length - 1][0]).toMatchObject({ sport: "football" })
    );

    fireEvent.click(screen.getByRole("button", { name: "🏀 Euroleague" }));
    await waitFor(() =>
      expect(spy.mock.calls[spy.mock.calls.length - 1][0]).toMatchObject({
        sport: "basketball",
        time_scope: "all",
      })
    );
  });

  it("passes typed search text to the API", async () => {
    const spy = vi.spyOn(api, "getEvents").mockResolvedValue([]);
    renderPage();
    await waitFor(() => expect(spy).toHaveBeenCalled());

    fireEvent.change(screen.getByPlaceholderText("Takım veya lig ara..."), {
      target: { value: "Bayern" },
    });

    await waitFor(() =>
      expect(spy.mock.calls[spy.mock.calls.length - 1][0]).toMatchObject({ search: "Bayern" })
    );
  });

  it("debounces rapid search input and requests only the final value", async () => {
    const spy = vi.spyOn(api, "getEvents").mockResolvedValue([]);
    renderPage();
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    const input = screen.getByPlaceholderText("Takım veya lig ara...");

    fireEvent.change(input, { target: { value: "Bay" } });
    fireEvent.change(input, { target: { value: "Bayern" } });

    expect(spy).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2), { timeout: 1000 });
    expect(spy.mock.calls[1][0]).toMatchObject({ search: "Bayern" });
  });

  it("shows a loading skeleton while the first request is in flight", async () => {
    let resolve!: (events: EventListItem[]) => void;
    vi.spyOn(api, "getEvents").mockImplementation(
      () => new Promise((res) => { resolve = res; })
    );
    renderPage();

    expect(screen.getByTestId("events-loading")).toBeInTheDocument();

    await act(async () => {
      resolve([]);
    });
    await waitFor(() => expect(screen.queryByTestId("events-loading")).not.toBeInTheDocument());
  });

  it("shows a no-data-collected message when there are no filters and the API returns zero events", async () => {
    vi.spyOn(api, "getEvents").mockResolvedValue([]);
    renderPage();

    expect(await screen.findByText(/Henüz veri toplanmadı/)).toBeInTheDocument();
    expect(screen.queryByText(/Filtrelere uygun/)).not.toBeInTheDocument();
  });

  it("shows a distinct filtered-empty message when a search returns zero results", async () => {
    const spy = vi.spyOn(api, "getEvents").mockResolvedValue([]);
    renderPage();
    await waitFor(() => expect(spy).toHaveBeenCalled());

    fireEvent.change(screen.getByPlaceholderText("Takım veya lig ara..."), {
      target: { value: "Nonexistent" },
    });

    expect(await screen.findByText(/Filtrelere uygun/)).toBeInTheDocument();
    expect(screen.queryByText(/Henüz veri toplanmadı/)).not.toBeInTheDocument();
  });

  it("renders positive groups auto-expanded and ordinary groups collapsed by default", async () => {
    vi.spyOn(api, "getEvents").mockResolvedValue([positiveGermanEvent, ordinaryTurkishEvent]);
    renderPage();

    await screen.findByText("Almanya");
    // Positive country (Germany) starts expanded: its match is immediately visible.
    expect(screen.getByText(/Bayern/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Almanya/ })).toHaveAttribute("aria-expanded", "true");

    // Ordinary country (Turkey, no positive events) starts collapsed.
    expect(screen.queryByText(/Galatasaray/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Türkiye/ })).toHaveAttribute("aria-expanded", "false");
  });

  it("expands a country then its league independently on click, each a >=44px touch target", async () => {
    vi.spyOn(api, "getEvents").mockResolvedValue([positiveGermanEvent, ordinaryTurkishEvent]);
    renderPage();
    await screen.findByText("Türkiye");

    const turkeyButton = screen.getByRole("button", { name: /Türkiye/ });
    expect(turkeyButton).toHaveClass("touch-target");

    // Expanding the country reveals the league row, but the match stays hidden
    // until the league itself is expanded (two independent collapse levels).
    fireEvent.click(turkeyButton);
    expect(turkeyButton).toHaveAttribute("aria-expanded", "true");
    const leagueButton = await screen.findByRole("button", { name: /Süper Lig/ });
    expect(leagueButton).toHaveClass("touch-target");
    expect(leagueButton).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(/Galatasaray/)).not.toBeInTheDocument();

    fireEvent.click(leagueButton);
    expect(await screen.findByText(/Galatasaray/)).toBeInTheDocument();
    expect(leagueButton).toHaveAttribute("aria-expanded", "true");

    fireEvent.click(leagueButton);
    await waitFor(() => expect(screen.queryByText(/Galatasaray/)).not.toBeInTheDocument());
  });

  it("temporarily expands matching groups while searching, without overwriting the stored manual expansion", async () => {
    vi.spyOn(api, "getEvents").mockImplementation(async (params) => {
      const search = typeof params === "object" ? params?.search : undefined;
      if (search === "Galatasaray") return [ordinaryTurkishEvent];
      return [positiveGermanEvent, ordinaryTurkishEvent];
    });
    renderPage();
    await screen.findByText("Türkiye");
    // Turkey starts collapsed (no positive events).
    expect(screen.queryByText(/Galatasaray/)).not.toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText("Takım veya lig ara..."), {
      target: { value: "Galatasaray" },
    });

    // Search results only contain the Turkish match; it must show up even though the
    // Turkey group was never manually expanded.
    expect(await screen.findByText(/Galatasaray/)).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText("Takım veya lig ara..."), { target: { value: "" } });

    // Clearing search restores the original (collapsed) manual state, proving the
    // temporary search expansion was never persisted as a manual toggle.
    await screen.findByText("Türkiye");
    await waitFor(() => expect(screen.queryByText(/Galatasaray/)).not.toBeInTheDocument());
  });

  it("persists manually expanded groups across a remount within the session", async () => {
    vi.spyOn(api, "getEvents").mockResolvedValue([positiveGermanEvent, ordinaryTurkishEvent]);
    const { unmount } = renderPage();
    await screen.findByText("Türkiye");

    fireEvent.click(screen.getByRole("button", { name: /Türkiye/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Süper Lig/ }));
    await screen.findByText(/Galatasaray/);
    unmount();

    renderPage();
    await screen.findByText("Türkiye");
    expect(await screen.findByText(/Galatasaray/)).toBeInTheDocument();
  });

  it("opens the filter sheet, applies a country+league selection, shows the selected count, and requests filtered events", async () => {
    const spy = vi.spyOn(api, "getEvents").mockResolvedValue([]);
    renderPage();
    await waitFor(() => expect(spy).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("button", { name: /Filtrele/ }));
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
  });

  it("clears all filters via the Clear All action", async () => {
    const spy = vi.spyOn(api, "getEvents").mockResolvedValue([]);
    renderPage();
    await waitFor(() => expect(spy).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("button", { name: /Filtrele/ }));
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

  it("clearing a saved filter sends an explicit empty override, not an omitted param that would revert to the stale saved filter", async () => {
    // Finding 3: an empty array serializes to no query param at all, which
    // the backend reads as "omitted -> use my saved filter". If the user has
    // a real saved filter and clears it in the UI, the request must still
    // exclude everything (use_saved_filters:false), never silently fall back
    // to the still-saved narrowed set.
    vi.spyOn(api, "getSavedFilters").mockResolvedValue({
      country_ids: ["country:DE"],
      league_ids: [],
    });
    // Non-empty so the "no results" empty state (which also renders a
    // "Filtreleri Temizle" button matching a loose /Filtrele/ name query)
    // never appears and collides with the "Filtrele" button below.
    const spy = vi.spyOn(api, "getEvents").mockResolvedValue([positiveGermanEvent]);
    renderPage();
    // The freshly-loaded saved filter matches "confirmed", so the initial
    // request omits country_id/league_id entirely and relies on the
    // backend's own saved-filter resolution -- proven by the selected-count
    // badge (1) once the saved DE filter has synced into local state.
    await waitFor(() => expect(screen.getByText("1")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /Filtrele/ }));
    fireEvent.click(screen.getByRole("button", { name: /Tümünü Temizle/ }));

    await waitFor(() => {
      const lastCall = spy.mock.calls[spy.mock.calls.length - 1][0];
      expect(lastCall).toMatchObject({
        country_ids: [],
        league_ids: [],
        use_saved_filters: false,
      });
    });
  });

  it("omits country/league query params on first load, letting the backend apply the saved default", async () => {
    const spy = vi.spyOn(api, "getEvents").mockResolvedValue([]);
    renderPage();

    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy.mock.calls[0][0]).not.toHaveProperty("country_ids");
    expect(spy.mock.calls[0][0]).not.toHaveProperty("league_ids");
  });

  it("loads and saves filters scoped to the events preference key, independent of the opportunities scope", async () => {
    const eventsFilters = { country_ids: ["country:TR"], league_ids: [] };
    const opportunitiesFilters = { country_ids: ["country:DE"], league_ids: ["league:xyz"] };
    vi.mocked(api.getSavedFilters).mockImplementation(async (scope) =>
      scope === "events" ? eventsFilters : opportunitiesFilters
    );
    const saveSpy = vi.mocked(api.saveFilters).mockImplementation(async (_scope, filters) => filters);
    // Non-empty so the "no results" empty state (which also renders a
    // "Filtreleri Temizle" button matching a loose /Filtrele/ name query)
    // never appears here and collides with the "Filtrele" button below.
    vi.spyOn(api, "getEvents").mockResolvedValue([positiveGermanEvent]);
    renderPage();

    // Seeded from the events-scoped saved value only, never the opportunities one.
    await waitFor(() => expect(screen.getByText("1")).toBeInTheDocument());
    expect(api.getSavedFilters).toHaveBeenCalledWith("events");
    expect(api.getSavedFilters).not.toHaveBeenCalledWith("opportunities");

    fireEvent.click(screen.getByRole("button", { name: /Filtrele/ }));
    fireEvent.click(await screen.findByRole("checkbox", { name: /Almanya/ }));

    await waitFor(() => expect(saveSpy).toHaveBeenCalled(), { timeout: 1000 });
    // The debounced write persists to the events scope, never opportunities.
    expect(saveSpy.mock.calls.every(([scope]) => scope === "events")).toBe(true);
  });

  it("scopes the saved filter selection to the authenticated user, refetching fresh state on a user switch", async () => {
    // Deliberately different selection *counts* (1 vs 2) so the badge text
    // itself proves which user's data is showing, not just that some count renders.
    const userOneFilters = { country_ids: ["country:DE"], league_ids: [] };
    const userTwoFilters = { country_ids: ["country:TR"], league_ids: ["league:extra"] };
    const getSavedFiltersMock = vi.mocked(api.getSavedFilters).mockResolvedValue(userOneFilters);
    vi.spyOn(api, "getEvents").mockResolvedValue([]);

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const { rerender } = render(
      <QueryClientProvider client={queryClient}>
        <AllEventsPage userId={1} />
      </QueryClientProvider>
    );

    await waitFor(() => expect(screen.getByText("1")).toBeInTheDocument());
    expect(getSavedFiltersMock).toHaveBeenCalledTimes(1);

    getSavedFiltersMock.mockResolvedValue(userTwoFilters);
    rerender(
      <QueryClientProvider client={queryClient}>
        <AllEventsPage userId={2} />
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
    vi.spyOn(api, "getEvents").mockResolvedValue([]);
    const saveSpy = vi
      .mocked(api.saveFilters)
      .mockRejectedValueOnce(new Error("network down"))
      .mockResolvedValueOnce({ country_ids: ["country:DE"], league_ids: [] });
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: /Filtrele/ }));
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

  it("shows an inline error banner and keeps prior matches visible when a background refetch fails", async () => {
    vi.spyOn(api, "getEvents").mockResolvedValueOnce([positiveGermanEvent]);
    const { queryClient } = renderPage();
    await screen.findByText(/Bayern/);

    vi.spyOn(api, "getEvents").mockRejectedValueOnce(new Error("network down"));
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: ["events"] });
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(/güncellenemedi/);
    expect(screen.getByText(/Bayern/)).toBeInTheDocument();
  });

  it("shows a full error state with no data when the very first request fails", async () => {
    vi.spyOn(api, "getEvents").mockRejectedValue(new Error("network down"));
    renderPage();

    expect(await screen.findByText(/Veriler yüklenemedi/)).toBeInTheDocument();
    expect(screen.queryByText(/Bayern/)).not.toBeInTheDocument();
  });

  it("renders all three source odds columns and positive-return styling on a match card", async () => {
    vi.spyOn(api, "getEvents").mockResolvedValue([positiveGermanEvent]);
    renderPage();
    await screen.findByText(/Bayern/);

    const card = screen.getByText(/Bayern/).closest("div[class*='rounded']") as HTMLElement;
    const withinCard = within(card);
    expect(withinCard.getByText("Kaynak C")).toBeInTheDocument();
    expect(withinCard.getByText("Kaynak B")).toBeInTheDocument();
    expect(withinCard.getByText("Kaynak A")).toBeInTheDocument();
    expect(withinCard.getByText("Kaynak D")).toBeInTheDocument();
    expect(screen.getByText("+%5.50")).toHaveClass("text-emerald-300");
  });
});
