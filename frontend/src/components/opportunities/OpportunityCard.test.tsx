import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, vi } from "vitest";
import { OpportunityCard } from "./OpportunityCard";
import { CalculatedOpportunity } from "../../types/api";

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

describe("OpportunityCard", () => {
  it("renders match details, odds, and profit with cashback label", () => {
    const handleClick = vi.fn();
    renderWithClient(<OpportunityCard opportunity={mockOpportunity} onClick={handleClick} />);

    expect(screen.getByText(/Real Madrid/)).toBeInTheDocument();
    expect(screen.getByText(/Manchester City/)).toBeInTheDocument();
    expect(screen.getByText("Champions League")).toBeInTheDocument();
    expect(screen.getByText(/%10 KB Dahil/)).toBeInTheDocument();
    expect(screen.getByText(/%7.20 Net Fark/)).toBeInTheDocument();

    fireEvent.click(screen.getByText(/Real Madrid/));
    expect(handleClick).toHaveBeenCalled();
  });

  it("renders country and league as secondary context below the matchup title, without overwhelming it", () => {
    const withTaxonomy: CalculatedOpportunity = {
      ...mockOpportunity,
      country_id: "country:DE",
      country_name: "Almanya",
      country_iso_code: "DE",
      league_id: "league:00000000-0000-4000-8000-000000000001",
      league_name: "Bundesliga",
    };
    renderWithClient(<OpportunityCard opportunity={withTaxonomy} onClick={vi.fn()} />);

    const title = screen.getByRole("heading", { level: 2 });
    expect(title).toHaveTextContent(/Real Madrid/);

    const context = screen.getByText(/Bundesliga/);
    expect(context).toBeInTheDocument();
    expect(context).toHaveTextContent(/Almanya/);

    // Secondary context must sit below (follow, in DOM order) the matchup title...
    expect(
      title.compareDocumentPosition(context) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy();
    // ...and must not carry the title's large/bold styling, so it reads as secondary.
    expect(context.className).not.toContain("text-base");
    expect(context.className).not.toContain("font-bold");
  });

  it("gracefully omits country/league context when taxonomy fields are null", () => {
    renderWithClient(<OpportunityCard opportunity={mockOpportunity} onClick={vi.fn()} />);
    expect(screen.queryByText("Almanya")).not.toBeInTheDocument();
    expect(screen.queryByText("Bundesliga")).not.toBeInTheDocument();
  });

  it("warns that a bet losing money without cashback is not arbitrage", () => {
    // A negative standard profit means the odds carry a house edge: the bet only shows a
    // profit because the cashback model is applied. Presenting that as a surebet is the
    // failure this guards against.
    const cashbackDependent: CalculatedOpportunity = {
      ...mockOpportunity,
      min_standard_profit_cents: -70508,
      min_standard_profit_percent: -7.05,
      min_adjusted_profit_cents: 4370,
      min_adjusted_profit_percent: 0.44,
    };

    renderWithClient(<OpportunityCard opportunity={cashbackDependent} onClick={vi.fn()} />);

    const warning = screen.getByTestId("cashback-dependent-warning");
    expect(warning).toHaveTextContent(/Arbitraj değil/i);
    expect(warning).toHaveTextContent(/-7\.05/);
  });

  it("shows no such warning when the bet profits without cashback", () => {
    renderWithClient(<OpportunityCard opportunity={mockOpportunity} onClick={vi.fn()} />);
    expect(screen.queryByTestId("cashback-dependent-warning")).not.toBeInTheDocument();
  });

  it("renders alternatives accordion and allows selecting an alternative", () => {
    const alternative: CalculatedOpportunity = {
      ...mockOpportunity,
      opportunity_id: "alt:1",
      min_adjusted_profit_cents: 61000,
      min_adjusted_profit_percent: 0.61,
    };
    const oppWithAlternatives: CalculatedOpportunity = {
      ...mockOpportunity,
      alternatives: [alternative],
    };

    const handleSelectAlt = vi.fn();
    renderWithClient(
      <OpportunityCard
        opportunity={oppWithAlternatives}
        onClick={vi.fn()}
        onSelectAlternative={handleSelectAlt}
      />
    );

    const toggleButton = screen.getByTestId("toggle-alternatives-button");
    expect(toggleButton).toHaveTextContent(/Diğer Kombinasyonlar \(1\)/);

    // Click to expand
    fireEvent.click(toggleButton);
    expect(screen.getByText(/0\.61%/)).toBeInTheDocument();

    // Click alternative to select
    fireEvent.click(screen.getByText(/0\.61%/));
    expect(handleSelectAlt).toHaveBeenCalledWith(alternative);
  });

  it("renders 2-way market (OU25) with 2 columns and market badge", () => {
    const ouOpportunity: CalculatedOpportunity = {
      ...mockOpportunity,
      market_type: "OU25",
      market_label: "2.5 Gol Alt/Üst",
      selections: {
        OVER: { outcome: "OVER", source: "source_c", odds: "1.95", stake_cents: 50000, cashback_eligible: true },
        UNDER: { outcome: "UNDER", source: "source_b", odds: "1.95", stake_cents: 50000, cashback_eligible: true },
      },
      scenarios: {
        OVER: mockOpportunity.scenarios.HOME,
        UNDER: mockOpportunity.scenarios.AWAY,
      },
    };

    renderWithClient(<OpportunityCard opportunity={ouOpportunity} onClick={vi.fn()} />);

    expect(screen.getByText("2.5 Alt/Üst")).toBeInTheDocument();
    expect(screen.getByText("2.5 Üst")).toBeInTheDocument();
    expect(screen.getByText("2.5 Alt")).toBeInTheDocument();
  });

  it("renders basketball Moneyline opportunity with 🏀 Euroleague and Maç Kazananı badges", () => {
    const mlOpportunity: CalculatedOpportunity = {
      ...mockOpportunity,
      sport: "basketball",
      market_type: "BASKETBALL_ML",
      market_label: "Maç Kazananı (Uzatmalar Dahil)",
      selections: {
        HOME: { outcome: "HOME", source: "source_c", odds: "2.10", stake_cents: 48000, cashback_eligible: true },
        AWAY: { outcome: "AWAY", source: "source_b", odds: "2.10", stake_cents: 52000, cashback_eligible: true },
      },
      scenarios: {
        HOME: mockOpportunity.scenarios.HOME,
        AWAY: mockOpportunity.scenarios.AWAY,
      },
    };

    renderWithClient(<OpportunityCard opportunity={mlOpportunity} onClick={vi.fn()} />);

    expect(screen.getByTestId("basketball-badge")).toHaveTextContent("Euroleague");
    expect(screen.getByText("Maç Kazananı")).toBeInTheDocument();
    expect(screen.getByText("1 (Ev)")).toBeInTheDocument();
    expect(screen.getByText("2 (Dep)")).toBeInTheDocument();
  });

  it("renders basketball Asian Handicap opportunity with handicap spread lines", () => {
    const ahOpportunity: CalculatedOpportunity = {
      ...mockOpportunity,
      sport: "basketball",
      market_type: "BASKETBALL_AH",
      market_label: "Asya Handikap (-4.5 / +4.5)",
      selections: {
        HOME: { outcome: "HOME", source: "source_c", odds: "1.92", stake_cents: 50000, cashback_eligible: true },
        AWAY: { outcome: "AWAY", source: "source_a", odds: "1.98", stake_cents: 50000, cashback_eligible: false },
      },
      scenarios: {
        HOME: mockOpportunity.scenarios.HOME,
        AWAY: mockOpportunity.scenarios.AWAY,
      },
    };

    renderWithClient(<OpportunityCard opportunity={ahOpportunity} onClick={vi.fn()} />);

    expect(screen.getByTestId("basketball-badge")).toHaveTextContent("Euroleague");
    expect(screen.getByText("Asya Handikap (-4.5 / +4.5)")).toBeInTheDocument();
    expect(screen.getByText("1 (Ev)")).toBeInTheDocument();
    expect(screen.getByText("2 (Dep)")).toBeInTheDocument();
  });
});

