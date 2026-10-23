from datetime import UTC, datetime
from decimal import Decimal

import pytest
from ortools.sat.python import cp_model

from surebet.domain.enums import Outcome, Source
from surebet.domain.models import Quote
from surebet.matching.models import MatchedEvent
from surebet.opportunities.strategy import (
    calculate_event_opportunities,
    solve_assignment_opportunity,
)
from surebet.taxonomy.models import TaxonomyStatus

NOW = datetime(2026, 8, 24, 15, 0, 0, tzinfo=UTC)
KICKOFF = datetime(2026, 8, 25, 20, 0, 0, tzinfo=UTC)


def _make_quotes_dict(
    source_c: tuple[str, str, str] | None = None,
    source_b: tuple[str, str, str] | None = None,
    source_a: tuple[str, str, str] | None = None,
) -> dict[tuple[Source, Outcome], Quote]:
    quotes: dict[tuple[Source, Outcome], Quote] = {}
    if source_c:
        for outcome, price in zip(Outcome, source_c, strict=True):
            quotes[(Source.SOURCE_C, outcome)] = Quote(
                source=Source.SOURCE_C,
                event_id="source_c_1",
                outcome=outcome,
                odds=Decimal(price),
                observed_at=NOW,
            )
    if source_b:
        for outcome, price in zip(Outcome, source_b, strict=True):
            quotes[(Source.SOURCE_B, outcome)] = Quote(
                source=Source.SOURCE_B,
                event_id="source_b_1",
                outcome=outcome,
                odds=Decimal(price),
                observed_at=NOW,
            )
    if source_a:
        for outcome, price in zip(Outcome, source_a, strict=True):
            quotes[(Source.SOURCE_A, outcome)] = Quote(
                source=Source.SOURCE_A,
                event_id="source_a_1",
                outcome=outcome,
                odds=Decimal(price),
                observed_at=NOW,
            )
    return quotes


def test_independent_per_bet_cashback_math() -> None:
    # Test case: HOME=SOURCE_C @ 2.50, DRAW=SOURCE_A @ 3.40, AWAY=SOURCE_B @ 2.80
    # Budget = 1000 TL = 100,000 cents
    # HOME is Source C (eligible), DRAW is Source A (ineligible), AWAY is Source B (eligible)
    quotes = {
        Outcome.HOME: Quote(
            source=Source.SOURCE_C,
            event_id="e1",
            outcome=Outcome.HOME,
            odds=Decimal("2.50"),
            observed_at=NOW,
        ),
        Outcome.DRAW: Quote(
            source=Source.SOURCE_A,
            event_id="e1",
            outcome=Outcome.DRAW,
            odds=Decimal("3.40"),
            observed_at=NOW,
        ),
        Outcome.AWAY: Quote(
            source=Source.SOURCE_B,
            event_id="e1",
            outcome=Outcome.AWAY,
            odds=Decimal("2.80"),
            observed_at=NOW,
        ),
    }

    opp = solve_assignment_opportunity(
        quotes=quotes,
        budget_cents=100_000,
        now=NOW,
    )

    assert opp is not None
    # Check total stakes equal budget
    total_stakes = sum(s.stake_cents for s in opp.selections.values())
    assert total_stakes == 100_000
    assert opp.selections[Outcome.HOME].event_id == "e1"

    # In HOME scenario:
    # Winner: HOME (SOURCE_C). Losing: DRAW (SOURCE_A - 0 cashback), AWAY (SOURCE_B - eligible for 10% cashback)
    home_scenario = opp.scenarios[Outcome.HOME]
    away_stake = opp.selections[Outcome.AWAY].stake_cents
    assert home_scenario.losing_eligible_stakes_cents == away_stake
    assert home_scenario.cashback_cents == away_stake // 10
    assert (
        home_scenario.total_return_cents
        == home_scenario.gross_payout_cents + home_scenario.cashback_cents
    )
    assert home_scenario.net_profit_cents == home_scenario.total_return_cents - 100_000

    # In DRAW scenario:
    # Winner: DRAW (SOURCE_A). Losing: HOME (SOURCE_C - eligible), AWAY (SOURCE_B - eligible)
    draw_scenario = opp.scenarios[Outcome.DRAW]
    home_stake = opp.selections[Outcome.HOME].stake_cents
    assert draw_scenario.losing_eligible_stakes_cents == home_stake + away_stake
    assert draw_scenario.cashback_cents == (home_stake + away_stake) // 10


def test_assignment_avoids_pathological_integer_division_constraints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Benfica-style prices must use the exact model that does not stall CP-SAT."""
    real_solve = cp_model.CpSolver.solve

    def solve_without_integer_division(
        solver: cp_model.CpSolver, model: cp_model.CpModel
    ) -> cp_model.CpSolverStatus:
        if any(constraint.has_int_div() for constraint in model.proto.constraints):
            raise AssertionError("integer division constraints can stall on valid provider odds")
        return real_solve(solver, model)

    monkeypatch.setattr(cp_model.CpSolver, "solve", solve_without_integer_division)
    quotes = {
        Outcome.HOME: Quote(
            source=Source.SOURCE_A,
            event_id="benfica-estoril",
            outcome=Outcome.HOME,
            odds=Decimal("1.10"),
            observed_at=NOW,
        ),
        Outcome.DRAW: Quote(
            source=Source.SOURCE_C,
            event_id="benfica-estoril",
            outcome=Outcome.DRAW,
            odds=Decimal("8.00"),
            observed_at=NOW,
        ),
        Outcome.AWAY: Quote(
            source=Source.SOURCE_C,
            event_id="benfica-estoril",
            outcome=Outcome.AWAY,
            odds=Decimal("15.00"),
            observed_at=NOW,
        ),
    }

    opportunity = solve_assignment_opportunity(quotes, budget_cents=100_000, now=NOW)

    assert opportunity is not None
    assert sum(selection.stake_cents for selection in opportunity.selections.values()) == 100_000


def test_default_source_policy_excludes_source_a_from_ms1_and_ms2() -> None:
    # Source A has great HOME odds (3.00), but by default Source A is only allowed for DRAW
    quotes_dict = _make_quotes_dict(
        source_c=("2.10", "3.20", "2.80"),
        source_b=("2.15", "3.25", "2.85"),
        source_a=("3.50", "3.60", "3.50"),
    )

    event = MatchedEvent(
        matched_id="event_1",
        taxonomy_status=TaxonomyStatus.VERIFIED,
        competition="Premier League",
        country_id="country:GB",
        country_name="United Kingdom",
        country_iso_code="GB",
        league_id="league:00000000-0000-4000-8000-000000000002",
        league_name="Premier League",
        home="Arsenal",
        away="Chelsea",
        kickoff=KICKOFF,
        source_events={Source.SOURCE_C: "m1", Source.SOURCE_B: "j1", Source.SOURCE_A: "s1"},
        quotes=quotes_dict,
    )

    opps_default = calculate_event_opportunities(
        event=event,
        budget_cents=100_000,
        include_source_a_match_sides=False,
        now=NOW,
    )

    # In default mode, no opportunity should use Source A on HOME or AWAY
    for opp in opps_default:
        assert opp.selections[Outcome.HOME].source != Source.SOURCE_A
        assert opp.selections[Outcome.AWAY].source != Source.SOURCE_A
        # DRAW may use Source A
        if opp.selections[Outcome.DRAW].source == Source.SOURCE_A:
            assert opp.selections[Outcome.DRAW].cashback_eligible is False

    # When switch is enabled, Source A may be used on HOME/AWAY
    opps_source_a_enabled = calculate_event_opportunities(
        event=event,
        budget_cents=100_000,
        include_source_a_match_sides=True,
        now=NOW,
    )

    source_a_used_on_sides = any(
        opp.selections[Outcome.HOME].source == Source.SOURCE_A
        or opp.selections[Outcome.AWAY].source == Source.SOURCE_A
        for opp in opps_source_a_enabled
    )
    assert source_a_used_on_sides is True


def test_unmapped_event_never_generates_opportunity() -> None:
    event = MatchedEvent(
        matched_id="event_1",
        taxonomy_status=TaxonomyStatus.UNMAPPED,
        competition="Premier League",
        country_id=None,
        country_name=None,
        country_iso_code=None,
        league_id=None,
        league_name=None,
        home="Arsenal",
        away="Chelsea",
        kickoff=KICKOFF,
        source_events={Source.SOURCE_C: "m1", Source.SOURCE_B: "j1", Source.SOURCE_A: "s1"},
        quotes=_make_quotes_dict(
            source_c=("2.10", "3.20", "2.80"),
            source_b=("2.15", "3.25", "2.85"),
            source_a=("3.50", "3.60", "3.50"),
        ),
    )

    assert calculate_event_opportunities(event, 100_000, False, NOW) == ()


def _event(quotes: dict[tuple[Source, Outcome], Quote]) -> MatchedEvent:
    return MatchedEvent(
        matched_id="event_1",
        taxonomy_status=TaxonomyStatus.VERIFIED,
        competition="Premier League",
        country_id="country:GB",
        country_name="United Kingdom",
        country_iso_code="GB",
        league_id="league:00000000-0000-4000-8000-000000000002",
        league_name="Premier League",
        home="Arsenal",
        away="Chelsea",
        kickoff=KICKOFF,
        source_events={Source.SOURCE_C: "m1", Source.SOURCE_B: "j1", Source.SOURCE_A: "s1"},
        quotes=quotes,
    )


def test_single_operator_cashback_assignments_are_evaluated() -> None:
    """Pure single-brand WAMP feed assignments (all SOURCE_C or all SOURCE_B) earn 10% losing cashback
    and must be evaluated, while redundant mixed cross-brand assignments (e.g. SOURCE_C-SOURCE_B-SOURCE_C) are skipped."""
    event = _event(
        _make_quotes_dict(source_c=("2.10", "3.20", "2.80"), source_b=("2.10", "3.20", "2.80"))
    )

    opportunities = calculate_event_opportunities(
        event=event, budget_cents=100_000, include_source_a_match_sides=False, now=NOW
    )

    # Exactly 2 opportunities: pure SOURCE_C (all 3 legs) and pure SOURCE_B (all 3 legs), no cross-brand duplicates
    assert len(opportunities) == 2
    sources_per_opp = [{s.source for s in opp.selections.values()} for opp in opportunities]
    assert {Source.SOURCE_C} in sources_per_opp
    assert {Source.SOURCE_B} in sources_per_opp


def test_opportunities_include_cross_operator_and_single_brand_cashback() -> None:
    """Assignments must include cross-operator hedges and pure single-brand cashback hedges."""
    event = _event(
        _make_quotes_dict(
            source_c=("2.10", "3.20", "2.80"),
            source_b=("2.15", "3.25", "2.85"),
            source_a=("3.50", "3.60", "3.50"),
        )
    )

    opportunities = calculate_event_opportunities(
        event=event, budget_cents=100_000, include_source_a_match_sides=False, now=NOW
    )

    assert opportunities != ()
    # 4 cross-operator (Source A DRAW) + 2 single-brand (all-SOURCE_C, all-SOURCE_B) = 6 total
    assert len(opportunities) == 6
    cross_operator = [
        opp for opp in opportunities if opp.selections[Outcome.DRAW].source is Source.SOURCE_A
    ]
    assert len(cross_operator) == 4


def test_source_d_respects_include_source_a_match_sides() -> None:
    quotes = _make_quotes_dict(
        source_c=("2.10", "3.20", "2.80"),
        source_b=("2.15", "3.25", "2.85"),
    )
    # Source D has highest home odds 4.00
    for outcome, price in zip(Outcome, ("4.00", "3.60", "2.50"), strict=True):
        quotes[(Source.SOURCE_D, outcome)] = Quote(
            source=Source.SOURCE_D,
            event_id="source_d_1",
            outcome=outcome,
            odds=Decimal(price),
            observed_at=NOW,
        )

    event = MatchedEvent(
        matched_id="event_1",
        taxonomy_status=TaxonomyStatus.VERIFIED,
        competition="Premier League",
        country_id="country:GB",
        country_name="United Kingdom",
        country_iso_code="GB",
        league_id="league:00000000-0000-4000-8000-000000000002",
        league_name="Premier League",
        home="Arsenal",
        away="Chelsea",
        kickoff=KICKOFF,
        source_events={Source.SOURCE_C: "m1", Source.SOURCE_B: "j1", Source.SOURCE_D: "ss1"},
        quotes=quotes,
    )

    # When include_source_a_match_sides is False: Source D must NOT be used on HOME
    opps_disabled = calculate_event_opportunities(
        event=event,
        budget_cents=100_000,
        include_source_a_match_sides=False,
        now=NOW,
    )
    for opp in opps_disabled:
        assert opp.selections[Outcome.HOME].source is not Source.SOURCE_D
        assert opp.selections[Outcome.AWAY].source is not Source.SOURCE_D

    # When include_source_a_match_sides is True: Source D CAN be used on HOME
    opps_enabled = calculate_event_opportunities(
        event=event,
        budget_cents=100_000,
        include_source_a_match_sides=True,
        now=NOW,
    )
    assert any(opp.selections[Outcome.HOME].source is Source.SOURCE_D for opp in opps_enabled)


def test_three_leg_source_b_cashback_can_produce_positive_profit() -> None:
    """Toulouse vs Lille real-world odds: 3.80 / 3.60 / 1.93 on Source B produces +0.61% net profit with cashback."""
    source_b_quotes = (
        Quote(
            source=Source.SOURCE_B,
            event_id="j1",
            outcome=Outcome.HOME,
            odds=Decimal("3.80"),
            observed_at=NOW,
        ),
        Quote(
            source=Source.SOURCE_B,
            event_id="j1",
            outcome=Outcome.DRAW,
            odds=Decimal("3.60"),
            observed_at=NOW,
        ),
        Quote(
            source=Source.SOURCE_B,
            event_id="j1",
            outcome=Outcome.AWAY,
            odds=Decimal("1.9259"),
            observed_at=NOW,
        ),
    )
    quotes_dict = {
        (Source.SOURCE_B, Outcome.HOME): source_b_quotes[0],
        (Source.SOURCE_B, Outcome.DRAW): source_b_quotes[1],
        (Source.SOURCE_B, Outcome.AWAY): source_b_quotes[2],
    }
    event = MatchedEvent(
        matched_id="toulouse_lille",
        taxonomy_status=TaxonomyStatus.VERIFIED,
        competition="Ligue 1",
        country_id="country:FR",
        country_name="France",
        country_iso_code="FR",
        league_id="league:fr1",
        league_name="Ligue 1",
        home="Toulouse",
        away="Lille",
        kickoff=KICKOFF,
        source_events={Source.SOURCE_B: "j1"},
        quotes=quotes_dict,
    )

    opps = calculate_event_opportunities(
        event=event,
        budget_cents=10_000_000,
        include_source_a_match_sides=False,
        now=NOW,
    )
    assert len(opps) == 1
    opp = opps[0]
    assert opp.is_positive is True
    assert opp.min_adjusted_profit_percent > 0.5
    assert opp.min_standard_profit_cents < 0  # Standard loss, but cashback positive
    for sel in opp.selections.values():
        assert sel.source is Source.SOURCE_B
        assert sel.cashback_eligible is True


def test_source_f_and_source_e_included_in_1x2_opportunities() -> None:
    quotes: dict[tuple[Source, Outcome], Quote] = {}
    quotes[(Source.SOURCE_F, Outcome.HOME)] = Quote(
        source=Source.SOURCE_F,
        event_id="source_f-1",
        outcome=Outcome.HOME,
        odds=Decimal("2.50"),
        observed_at=NOW,
    )
    quotes[(Source.SOURCE_A, Outcome.DRAW)] = Quote(
        source=Source.SOURCE_A,
        event_id="source_a-1",
        outcome=Outcome.DRAW,
        odds=Decimal("3.40"),
        observed_at=NOW,
    )
    quotes[(Source.SOURCE_E, Outcome.AWAY)] = Quote(
        source=Source.SOURCE_E,
        event_id="holi-1",
        outcome=Outcome.AWAY,
        odds=Decimal("2.80"),
        observed_at=NOW,
    )

    event = MatchedEvent(
        matched_id="source_f-holi-event",
        taxonomy_status=TaxonomyStatus.VERIFIED,
        competition="Premier League",
        country_id="country:GB",
        country_name="United Kingdom",
        country_iso_code="GB",
        league_id="league:00000000-0000-4000-8000-000000000002",
        league_name="Premier League",
        home="Arsenal",
        away="Chelsea",
        kickoff=KICKOFF,
        source_events={
            Source.SOURCE_F: "p1",
            Source.SOURCE_A: "s1",
            Source.SOURCE_E: "h1",
        },
        quotes=quotes,
    )

    opps = calculate_event_opportunities(
        event=event,
        budget_cents=100_000,
        include_source_a_match_sides=False,
        now=NOW,
    )

    assert len(opps) == 1
    opp = opps[0]
    assert opp.selections[Outcome.HOME].source == Source.SOURCE_F
    assert opp.selections[Outcome.HOME].cashback_eligible is True
    assert opp.selections[Outcome.DRAW].source == Source.SOURCE_A
    assert opp.selections[Outcome.DRAW].cashback_eligible is False
    assert opp.selections[Outcome.AWAY].source == Source.SOURCE_E
    assert opp.selections[Outcome.AWAY].cashback_eligible is False
