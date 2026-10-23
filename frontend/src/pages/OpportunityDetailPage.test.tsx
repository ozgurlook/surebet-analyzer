import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, vi } from "vitest";
import { OpportunityDetailPage } from "./OpportunityDetailPage";
import { CalculatedOpportunity } from "../types/api";

function renderWithClient(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

const mockOpportunity: CalculatedOpportunity = {
  opportunity_id: "test:1",
  matched_id: "match_1",
  competition: "Champions League",
  country_id: null,
  country_name: null,
  country_iso_code: null,
  league_id: null,
  league_name: null,
  home: "Real Madrid",
  away: "Manchester City",
  kickoff: "2026-08-25T20:00:00Z",
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
};

describe("OpportunityDetailPage", () => {
  it("renders full breakdown with 3 scenario cards, stakes, and back navigation", () => {
    const handleBack = vi.fn();
    renderWithClient(<OpportunityDetailPage opportunity={mockOpportunity} onBack={handleBack} />);

    expect(screen.getByText("Fırsatlara Dön")).toBeInTheDocument();
    expect(screen.getByText("Senaryo 1: Ev Sahibi Kazanırsa")).toBeInTheDocument();
    expect(screen.getByText("Senaryo 2: Beraberlik Biterse")).toBeInTheDocument();
    expect(screen.getByText("Senaryo 3: Deplasman Kazanırsa")).toBeInTheDocument();
    expect(screen.getByText("Bağımsız Kupon Başına Cashback Varsayımı")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Fırsatlara Dön"));
    expect(handleBack).toHaveBeenCalled();
  });
});
