import React from "react";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { SourcesHealthPage } from "./SourcesHealthPage";

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <SourcesHealthPage />
    </QueryClientProvider>
  );
}

afterEach(() => vi.restoreAllMocks());

describe("SourcesHealthPage", () => {
  it("shows a real load error instead of unavailable zero-value source cards", async () => {
    vi.spyOn(api, "getSourcesHealth").mockRejectedValue(new Error("network down"));
    vi.spyOn(api, "getSettings").mockResolvedValue({
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
    });
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("Kaynak sağlığı yüklenemedi");
    expect(screen.queryByText("Kaynak C")).not.toBeInTheDocument();
  });

  it("shows the configured collection interval", async () => {
    vi.spyOn(api, "getSourcesHealth").mockResolvedValue({});
    vi.spyOn(api, "getSettings").mockResolvedValue({
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
    });
    renderPage();

    expect(await screen.findByText(/40 saniyede/)).toBeInTheDocument();
  });
});
