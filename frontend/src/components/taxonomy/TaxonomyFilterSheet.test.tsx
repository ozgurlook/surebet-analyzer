import React from "react";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { TaxonomyFilterSheet } from "./TaxonomyFilterSheet";

function renderSheet() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <TaxonomyFilterSheet
        open
        onClose={vi.fn()}
        filters={{ country_ids: [], league_ids: [] }}
        onChange={vi.fn()}
      />
    </QueryClientProvider>
  );
}

afterEach(() => vi.restoreAllMocks());

describe("TaxonomyFilterSheet request states", () => {
  it("shows loading feedback while filter options are requested", () => {
    vi.spyOn(api, "getCountries").mockImplementation(() => new Promise(() => undefined));
    vi.spyOn(api, "getLeagues").mockImplementation(() => new Promise(() => undefined));
    renderSheet();

    expect(screen.getByText("Filtre seçenekleri yükleniyor...")).toBeInTheDocument();
  });

  it("shows a retryable error when filter options cannot load", async () => {
    vi.spyOn(api, "getCountries").mockRejectedValue(new Error("network down"));
    vi.spyOn(api, "getLeagues").mockResolvedValue([]);
    renderSheet();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Filtre seçenekleri yüklenemedi"
    );
    expect(screen.getByRole("button", { name: "Tekrar Dene" })).toBeInTheDocument();
  });
});
