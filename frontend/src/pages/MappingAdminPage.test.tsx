import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "../api/client";
import {
  LeagueRecord,
  MappingItem,
  RegionSummary,
  UnmatchedItem,
} from "../types/api";
import { SettingsPage } from "./SettingsPage";
import { MappingAdminPage } from "./MappingAdminPage";

const pendingItem: UnmatchedItem = {
  source: "source_c",
  competition_id: "comp-1",
  identity_kind: "provider_id",
  competition_name: "Bundesliga",
  country_id: "country:DE",
  country_name: "Almanya",
  country_alias: null,
  season: "2026",
  kind: "league",
  gender: null,
  age_group: null,
  is_reserve: null,
  first_seen_at: "2026-08-20T09:00:00Z",
  last_seen_at: "2026-08-29T09:00:00Z",
  updated_at: "2026-08-29T09:00:00Z",
  method: "suggestion",
  confidence: 0.82,
  review_state: "pending",
  target: {
    league_id: "league:1",
    region_id: "country:DE",
    display_name: "Bundesliga",
    kind: "league",
    gender: null,
    age_group: null,
    is_reserve: null,
    active: true,
    updated_at: "2026-08-25T09:00:00Z",
  },
  conflict_reasons: [],
};

const conflictItem: UnmatchedItem = {
  ...pendingItem,
  source: "source_b",
  competition_id: "comp-2",
  identity_kind: "derived_text",
  competition_name: "Bilinmeyen Turnuva",
  country_id: null,
  country_name: null,
  country_alias: "Almanya",
  method: null,
  confidence: null,
  review_state: null,
  target: null,
  conflict_reasons: ["derived_text_identity", "unresolved_country"],
};

const mappingResponse: MappingItem = {
  source: "source_b",
  competition_id: "comp-2",
  league_id: "league:2",
  method: "manual",
  confidence: 1,
  review_state: "approved",
  note: null,
  created_at: "2026-08-30T09:00:00Z",
  updated_at: "2026-08-30T09:00:00Z",
};

const approvedManualItem: UnmatchedItem = {
  ...conflictItem,
  method: "manual",
  confidence: 1,
  review_state: "approved",
  target: pendingItem.target,
  updated_at: "2026-08-30T09:00:00Z",
};

const approvedAutomaticItem: UnmatchedItem = {
  ...pendingItem,
  method: "verified_source_id",
  confidence: 1,
  review_state: "approved",
  updated_at: "2026-08-30T10:00:00Z",
};

const approvedSuggestionItem: UnmatchedItem = {
  ...pendingItem,
  method: "suggestion",
  review_state: "approved",
  updated_at: "2026-08-30T11:00:00Z",
};

const leagueRecord: LeagueRecord = {
  league_id: "league:1",
  region_id: "country:DE",
  display_name: "Bundesliga",
  kind: "league",
  gender: null,
  age_group: null,
  is_reserve: null,
  active: true,
  updated_at: "2026-08-30T09:00:00Z",
};

const countriesFixture: RegionSummary[] = [
  {
    country_id: "country:DE",
    region_class: "country",
    iso_code: "DE",
    display_name: "Almanya",
    league_count: 1,
    event_count: 1,
    opportunity_count: 0,
    freshest_at: null,
  },
];

function renderPage(component: React.ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return {
    queryClient,
    ...render(<QueryClientProvider client={queryClient}>{component}</QueryClientProvider>),
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Settings entry point", () => {
  it("shows a Veri Eşleştirme row in Settings and opens the mapping admin page on click", async () => {
    vi.spyOn(api, "getSettings").mockResolvedValue({
      personal: {
        default_budget_cents: 100000,
        include_source_a_match_sides: false,
        tracked_competitions_only: true,
        updated_at: "2026-08-26T09:00:00Z",
      },
      system: {
        refresh_interval_seconds: 30,
        staleness_limit_seconds: 75,
        updated_at: "2026-08-26T09:00:00Z",
      },
    });
    const onOpenMappingAdmin = vi.fn();

    renderPage(
      <SettingsPage userId={1} username="alice" onLogout={vi.fn()} onOpenMappingAdmin={onOpenMappingAdmin} />
    );

    const row = await screen.findByRole("button", { name: /Veri Eşleştirme/ });
    fireEvent.click(row);
    expect(onOpenMappingAdmin).toHaveBeenCalledTimes(1);
  });
});

describe("MappingAdminPage", () => {
  it("switches to the mapped view and lists approved mappings", async () => {
    vi.spyOn(api, "getUnmatchedMappings").mockResolvedValue([]);
    const mappedSpy = vi
      .spyOn(api, "getMappedMappings")
      .mockResolvedValue([approvedManualItem, approvedAutomaticItem]);
    renderPage(<MappingAdminPage onBack={vi.fn()} />);

    fireEvent.click(await screen.findByRole("tab", { name: "Eşleştirilenler" }));

    await waitFor(() => expect(mappedSpy).toHaveBeenCalledTimes(1));
    expect(await screen.findByTestId("mapping-item-source_b-comp-2")).toBeInTheDocument();
    expect(screen.getByTestId("mapping-item-source_c-comp-1")).toBeInTheDocument();
  });

  it("undoes a previously saved manual mapping from the mapped view", async () => {
    vi.spyOn(api, "getUnmatchedMappings").mockResolvedValue([]);
    vi.spyOn(api, "getMappedMappings").mockResolvedValue([approvedManualItem]);
    const undoSpy = vi.spyOn(api, "undoMapping").mockResolvedValue(undefined);
    renderPage(<MappingAdminPage onBack={vi.fn()} />);

    fireEvent.click(await screen.findByRole("tab", { name: "Eşleştirilenler" }));
    fireEvent.click(await screen.findByTestId("mapping-item-source_b-comp-2"));
    fireEvent.click(await screen.findByRole("button", { name: "Geri Al" }));
    expect(undoSpy).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: "Evet, Geri Al" }));

    await waitFor(() =>
      expect(undoSpy).toHaveBeenCalledWith(
        "source_b",
        "comp-2",
        approvedManualItem.updated_at
      )
    );
  });

  it("undoes a previously approved suggestion from the mapped view", async () => {
    vi.spyOn(api, "getUnmatchedMappings").mockResolvedValue([]);
    vi.spyOn(api, "getMappedMappings").mockResolvedValue([approvedSuggestionItem]);
    const undoSpy = vi.spyOn(api, "undoMapping").mockResolvedValue(undefined);
    renderPage(<MappingAdminPage onBack={vi.fn()} />);

    fireEvent.click(await screen.findByRole("tab", { name: "Eşleştirilenler" }));
    fireEvent.click(await screen.findByTestId("mapping-item-source_c-comp-1"));
    fireEvent.click(await screen.findByRole("button", { name: "Geri Al" }));
    fireEvent.click(await screen.findByRole("button", { name: "Evet, Geri Al" }));

    await waitFor(() =>
      expect(undoSpy).toHaveBeenCalledWith(
        "source_c",
        "comp-1",
        approvedSuggestionItem.updated_at
      )
    );
  });

  it("shows automatic mappings without offering undo", async () => {
    vi.spyOn(api, "getUnmatchedMappings").mockResolvedValue([]);
    vi.spyOn(api, "getMappedMappings").mockResolvedValue([approvedAutomaticItem]);
    renderPage(<MappingAdminPage onBack={vi.fn()} />);

    fireEvent.click(await screen.findByRole("tab", { name: "Eşleştirilenler" }));
    fireEvent.click(await screen.findByTestId("mapping-item-source_c-comp-1"));

    expect(screen.queryByRole("button", { name: "Geri Al" })).not.toBeInTheDocument();
  });

  it("shows a loading state while fetching the review queue", async () => {
    let resolve!: (items: UnmatchedItem[]) => void;
    vi.spyOn(api, "getUnmatchedMappings").mockImplementation(
      () => new Promise((res) => { resolve = res; })
    );
    renderPage(<MappingAdminPage onBack={vi.fn()} />);

    expect(screen.getByTestId("mapping-loading")).toBeInTheDocument();
    resolve([]);
    await waitFor(() => expect(screen.queryByTestId("mapping-loading")).not.toBeInTheDocument());
  });

  it("shows an empty state when there is nothing to review", async () => {
    vi.spyOn(api, "getUnmatchedMappings").mockResolvedValue([]);
    renderPage(<MappingAdminPage onBack={vi.fn()} />);

    expect(await screen.findByText("Bekleyen eşleştirme yok.")).toBeInTheDocument();
  });

  it("shows an error state when the queue fails to load", async () => {
    vi.spyOn(api, "getUnmatchedMappings").mockRejectedValue(new Error("network down"));
    renderPage(<MappingAdminPage onBack={vi.fn()} />);

    expect(await screen.findByText(/Eşleştirme listesi yüklenemedi/)).toBeInTheDocument();
  });

  it("calls onBack when the back button is pressed", async () => {
    vi.spyOn(api, "getUnmatchedMappings").mockResolvedValue([]);
    const onBack = vi.fn();
    renderPage(<MappingAdminPage onBack={onBack} />);
    await screen.findByText("Bekleyen eşleştirme yok.");

    fireEvent.click(screen.getByRole("button", { name: "Geri Dön" }));
    expect(onBack).toHaveBeenCalledTimes(1);
  });

  it("lists evidence, suggested target, confidence, conflicts and review state for each item", async () => {
    vi.spyOn(api, "getUnmatchedMappings").mockResolvedValue([pendingItem, conflictItem]);
    renderPage(<MappingAdminPage onBack={vi.fn()} />);

    const pendingRow = await screen.findByTestId("mapping-item-source_c-comp-1");
    expect(within(pendingRow).getByText("Bundesliga")).toBeInTheDocument();
    expect(within(pendingRow).getByText(/Almanya/)).toBeInTheDocument();
    expect(within(pendingRow).getByText(/Sağlayıcı Kimliği/)).toBeInTheDocument();
    expect(within(pendingRow).getByText(/%82/)).toBeInTheDocument();
    expect(within(pendingRow).getByText(/Beklemede/)).toBeInTheDocument();
    expect(within(pendingRow).getByText(/Öneri: Bundesliga/)).toBeInTheDocument();

    const conflictRow = screen.getByTestId("mapping-item-source_b-comp-2");
    expect(within(conflictRow).getByText(/Metinden Türetildi/)).toBeInTheDocument();
    expect(within(conflictRow).getByText(/Ülke çözümlenemedi/)).toBeInTheDocument();
  });

  it("approves a pending suggestion after explicit confirmation and invalidates dependent caches", async () => {
    vi.spyOn(api, "getUnmatchedMappings").mockResolvedValue([pendingItem]);
    const approveSpy = vi.spyOn(api, "approveMappingSuggestion").mockResolvedValue({
      ...mappingResponse,
      source: "source_c",
      competition_id: "comp-1",
      method: "suggestion",
    });
    const { queryClient } = renderPage(<MappingAdminPage onBack={vi.fn()} />);
    const invalidations = vi.spyOn(queryClient, "invalidateQueries").mockResolvedValue(undefined);

    fireEvent.click(await screen.findByTestId("mapping-item-source_c-comp-1"));
    fireEvent.click(await screen.findByRole("button", { name: "Onayla" }));

    // Explicit confirmation step required before the mutation fires.
    expect(approveSpy).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: "Onayı Kaydet" }));

    await waitFor(() => expect(approveSpy).toHaveBeenCalledWith("source_c", "comp-1", {
      expected_updated_at: "2026-08-29T09:00:00Z",
    }));
    await waitFor(() =>
      expect(invalidations.mock.calls.map(([f]) => f?.queryKey)).toEqual(
        expect.arrayContaining([
          ["mappings"],
          ["countries"],
          ["leagues"],
          ["events"],
          ["opportunities"],
          ["dashboard"],
        ])
      )
    );
    expect(await screen.findByText(/onayland/i)).toBeInTheDocument();
  });

  it("links an unmatched competition to an existing league after selecting it and confirming", async () => {
    vi.spyOn(api, "getUnmatchedMappings").mockResolvedValue([conflictItem]);
    vi.spyOn(api, "getLeagues").mockResolvedValue([
      {
        league_id: "league:2",
        country_id: "country:DE",
        display_name: "2. Bundesliga",
        kind: "league",
        gender: null,
        age_group: null,
        is_reserve: null,
        active: true,
        event_count: 0,
        opportunity_count: 0,
        freshest_at: null,
      },
    ]);
    const linkSpy = vi.spyOn(api, "linkMapping").mockResolvedValue(mappingResponse);
    renderPage(<MappingAdminPage onBack={vi.fn()} />);

    fireEvent.click(await screen.findByTestId("mapping-item-source_b-comp-2"));
    fireEvent.click(await screen.findByRole("button", { name: "Başka Lige Bağla" }));

    // Validation: cannot confirm without a selection.
    fireEvent.click(await screen.findByRole("button", { name: "Bağla" }));
    expect(await screen.findByText("Lütfen bir lig seçin.")).toBeInTheDocument();
    expect(linkSpy).not.toHaveBeenCalled();

    fireEvent.click(await screen.findByRole("button", { name: "2. Bundesliga" }));
    fireEvent.click(screen.getByRole("button", { name: "Bağla" }));

    await waitFor(() =>
      expect(linkSpy).toHaveBeenCalledWith("source_b", "comp-2", {
        expected_updated_at: "2026-08-29T09:00:00Z",
        league_id: "league:2",
      })
    );

    // Undo affordance appears after a successful manual link.
    expect(await screen.findByRole("button", { name: "Geri Al" })).toBeInTheDocument();
  });

  it("undoes a manual link after explicit confirmation", async () => {
    vi.spyOn(api, "getUnmatchedMappings").mockResolvedValue([conflictItem]);
    vi.spyOn(api, "getLeagues").mockResolvedValue([
      {
        league_id: "league:2",
        country_id: "country:DE",
        display_name: "2. Bundesliga",
        kind: "league",
        gender: null,
        age_group: null,
        is_reserve: null,
        active: true,
        event_count: 0,
        opportunity_count: 0,
        freshest_at: null,
      },
    ]);
    vi.spyOn(api, "linkMapping").mockResolvedValue(mappingResponse);
    const removeSpy = vi.spyOn(api, "undoMapping").mockResolvedValue(undefined);
    renderPage(<MappingAdminPage onBack={vi.fn()} />);

    fireEvent.click(await screen.findByTestId("mapping-item-source_b-comp-2"));
    fireEvent.click(await screen.findByRole("button", { name: "Başka Lige Bağla" }));
    fireEvent.click(await screen.findByRole("button", { name: "2. Bundesliga" }));
    fireEvent.click(screen.getByRole("button", { name: "Bağla" }));
    fireEvent.click(await screen.findByRole("button", { name: "Geri Al" }));

    expect(removeSpy).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: "Evet, Geri Al" }));

    await waitFor(() =>
      expect(removeSpy).toHaveBeenCalledWith("source_b", "comp-2", mappingResponse.updated_at)
    );
  });

  it("creates a new league and links it when no target exists", async () => {
    vi.spyOn(api, "getUnmatchedMappings").mockResolvedValue([conflictItem]);
    vi.spyOn(api, "getCountries").mockResolvedValue(countriesFixture);
    const createSpy = vi.spyOn(api, "createAndLinkLeague").mockResolvedValue(mappingResponse);
    renderPage(<MappingAdminPage onBack={vi.fn()} />);

    fireEvent.click(await screen.findByTestId("mapping-item-source_b-comp-2"));
    fireEvent.click(await screen.findByRole("button", { name: "Yeni Lig Oluştur" }));

    fireEvent.click(await screen.findByRole("button", { name: "Oluştur ve Bağla" }));
    expect(await screen.findByText("Lütfen bölge ve lig adı girin.")).toBeInTheDocument();
    expect(createSpy).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("Bölge"), { target: { value: "country:DE" } });
    fireEvent.change(screen.getByLabelText("Lig Adı"), { target: { value: "Bilinmeyen Turnuva" } });
    fireEvent.click(screen.getByRole("button", { name: "Oluştur ve Bağla" }));

    await waitFor(() =>
      expect(createSpy).toHaveBeenCalledWith("source_b", "comp-2", expect.objectContaining({
        expected_updated_at: "2026-08-29T09:00:00Z",
        region_id: "country:DE",
        display_name: "Bilinmeyen Turnuva",
      }))
    );
  });

  it("renames the canonical league display name", async () => {
    vi.spyOn(api, "getUnmatchedMappings").mockResolvedValue([pendingItem]);
    const renameSpy = vi.spyOn(api, "renameLeague").mockResolvedValue(leagueRecord);
    renderPage(<MappingAdminPage onBack={vi.fn()} />);

    fireEvent.click(await screen.findByTestId("mapping-item-source_c-comp-1"));
    fireEvent.click(await screen.findByRole("button", { name: "Yeniden Adlandır" }));

    const input = await screen.findByLabelText("Yeni Lig Adı");
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Adı Kaydet" }));
    expect(await screen.findByText("Lig adı boş olamaz.")).toBeInTheDocument();
    expect(renameSpy).not.toHaveBeenCalled();

    fireEvent.change(input, { target: { value: "Bundesliga 1" } });
    fireEvent.click(screen.getByRole("button", { name: "Adı Kaydet" }));

    await waitFor(() =>
      expect(renameSpy).toHaveBeenCalledWith("league:1", {
        expected_updated_at: "2026-08-25T09:00:00Z",
        display_name: "Bundesliga 1",
      })
    );
  });

  it("closes the sheet, refetches, and shows a Turkish retry message on a 409 conflict", async () => {
    const listSpy = vi.spyOn(api, "getUnmatchedMappings").mockResolvedValue([pendingItem]);
    vi.spyOn(api, "approveMappingSuggestion").mockRejectedValue(
      new ApiError("stale", 409)
    );
    renderPage(<MappingAdminPage onBack={vi.fn()} />);

    fireEvent.click(await screen.findByTestId("mapping-item-source_c-comp-1"));
    fireEvent.click(await screen.findByRole("button", { name: "Onayla" }));
    fireEvent.click(await screen.findByRole("button", { name: "Onayı Kaydet" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(await screen.findByRole("alert")).toHaveTextContent(/tekrar deneyin/i);
    await waitFor(() => expect(listSpy.mock.calls.length).toBeGreaterThan(1));
  });
});
