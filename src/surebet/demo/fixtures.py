"""Hand-written fixture list for demo mode.

Team names are real clubs so matching and taxonomy behave as they would on live
data; every price is generated (see ``collector.py``) and none comes from a feed.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from surebet.domain.enums import Outcome, Source, Sport, TwoWayOutcome
from surebet.taxonomy.models import CompetitionKind

# Competition naming differs per protocol family, the way real feeds disagree.
WS_RPC = "ws_rpc"
WAMP_FEED = "wamp_feed"
HTTP_JSON = "http_json"

SOURCE_FAMILY: dict[Source, str] = {
    Source.SOURCE_A: WS_RPC,
    Source.SOURCE_B: WAMP_FEED,
    Source.SOURCE_C: WAMP_FEED,
    Source.SOURCE_D: HTTP_JSON,
    Source.SOURCE_E: WAMP_FEED,
    Source.SOURCE_F: WAMP_FEED,
}


@dataclass(frozen=True)
class DemoLeague:
    key: str
    country: str
    names: dict[str, str]


LEAGUES: dict[str, DemoLeague] = {
    league.key: league
    for league in (
        DemoLeague(
            "tr-super-lig",
            "Türkiye",
            {WS_RPC: "Süper Lig", WAMP_FEED: "Trendyol Süper Lig", HTTP_JSON: "Türkiye Süper Lig"},
        ),
        DemoLeague(
            "en-premier-league",
            "İngiltere",
            {
                WS_RPC: "Premier League",
                WAMP_FEED: "Premier Lig",
                HTTP_JSON: "İngiltere Premier Lig",
            },
        ),
        DemoLeague(
            "es-la-liga",
            "İspanya",
            {WS_RPC: "La Liga", WAMP_FEED: "LaLiga", HTTP_JSON: "İspanya La Liga"},
        ),
        DemoLeague(
            "it-serie-a",
            "İtalya",
            {WS_RPC: "Serie A", WAMP_FEED: "Serie A", HTTP_JSON: "İtalya Serie A"},
        ),
        DemoLeague(
            "fr-ligue-1",
            "Fransa",
            {WS_RPC: "Ligue 1", WAMP_FEED: "Ligue 1", HTTP_JSON: "Fransa Ligue 1"},
        ),
        DemoLeague(
            "de-bundesliga",
            "Almanya",
            {WS_RPC: "Bundesliga", WAMP_FEED: "Bundesliga", HTTP_JSON: "Almanya Bundesliga"},
        ),
        DemoLeague(
            "uefa-champions-league",
            "Uluslararası Kulüpler",
            {
                WS_RPC: "UEFA Şampiyonlar Ligi",
                WAMP_FEED: "Şampiyonlar Ligi",
                HTTP_JSON: "UEFA Şampiyonlar Ligi",
            },
        ),
        DemoLeague(
            "uefa-europa-league",
            "Uluslararası Kulüpler",
            {WS_RPC: "UEFA Avrupa Ligi", WAMP_FEED: "Avrupa Ligi", HTTP_JSON: "UEFA Avrupa Ligi"},
        ),
        DemoLeague(
            "uefa-conference-league",
            "Uluslararası Kulüpler",
            {
                WS_RPC: "UEFA Konferans Ligi",
                WAMP_FEED: "Konferans Ligi",
                HTTP_JSON: "UEFA Avrupa Konferans Ligi",
            },
        ),
        DemoLeague(
            "uefa-nations-league",
            "Uluslararası",
            {
                WS_RPC: "UEFA Uluslar Ligi",
                WAMP_FEED: "Uluslar Ligi",
                HTTP_JSON: "UEFA Uluslar Ligi",
            },
        ),
        DemoLeague(
            "bask-euroleague",
            "Uluslararası Kulüpler",
            {WS_RPC: "Euroleague", WAMP_FEED: "EuroLeague", HTTP_JSON: "Euroleague"},
        ),
    )
}


@dataclass(frozen=True)
class Edge:
    """One source pricing one outcome above the market, which is what opens a surebet."""

    source: Source
    outcome: Outcome | TwoWayOutcome
    multiplier: Decimal


@dataclass(frozen=True)
class DemoFixture:
    key: str
    league: str
    home: str
    away: str
    day: int
    local_time: str
    # Fair probabilities: (home, draw, away) for football, (home, away) for basketball.
    probabilities: tuple[Decimal, ...]
    over_probability: Decimal | None = None
    sport: Sport = Sport.FOOTBALL
    edges: tuple[Edge, ...] = field(default_factory=tuple)


def _p(*values: str) -> tuple[Decimal, ...]:
    return tuple(Decimal(value) for value in values)


A, B, D, E, F = (
    Source.SOURCE_A,
    Source.SOURCE_B,
    Source.SOURCE_D,
    Source.SOURCE_E,
    Source.SOURCE_F,
)
H, X, W2 = Outcome.HOME, Outcome.DRAW, Outcome.AWAY
OVER, UNDER = TwoWayOutcome.OVER, TwoWayOutcome.UNDER

FIXTURES: tuple[DemoFixture, ...] = (
    # Türkiye Süper Lig
    DemoFixture(
        "tr-gs-fb",
        "tr-super-lig",
        "Galatasaray",
        "Fenerbahçe",
        0,
        "20:00",
        _p("0.46", "0.26", "0.28"),
        Decimal("0.58"),
        edges=(
            Edge(B, H, Decimal("1.08")),
            Edge(A, X, Decimal("1.07")),
            Edge(E, W2, Decimal("1.14")),
        ),
    ),
    DemoFixture(
        "tr-bjk-ts",
        "tr-super-lig",
        "Beşiktaş",
        "Trabzonspor",
        0,
        "17:00",
        _p("0.50", "0.25", "0.25"),
        Decimal("0.55"),
    ),
    DemoFixture(
        "tr-ibfk-sam",
        "tr-super-lig",
        "Başakşehir",
        "Samsunspor",
        1,
        "14:30",
        _p("0.41", "0.29", "0.30"),
        Decimal("0.49"),
    ),
    DemoFixture(
        "tr-goz-kas",
        "tr-super-lig",
        "Göztepe",
        "Kasımpaşa",
        1,
        "19:00",
        _p("0.44", "0.28", "0.28"),
        Decimal("0.52"),
        edges=(Edge(F, H, Decimal("1.10")),),
    ),
    # Premier League
    DemoFixture(
        "en-ars-che",
        "en-premier-league",
        "Arsenal",
        "Chelsea",
        0,
        "19:30",
        _p("0.52", "0.25", "0.23"),
        Decimal("0.57"),
        edges=(Edge(B, W2, Decimal("1.15")), Edge(E, X, Decimal("1.05"))),
    ),
    DemoFixture(
        "en-liv-mci",
        "en-premier-league",
        "Liverpool",
        "Manchester City",
        1,
        "18:30",
        _p("0.40", "0.25", "0.35"),
        Decimal("0.66"),
    ),
    DemoFixture(
        "en-new-tot",
        "en-premier-league",
        "Newcastle United",
        "Tottenham",
        1,
        "17:00",
        _p("0.47", "0.24", "0.29"),
        Decimal("0.63"),
        edges=(Edge(D, OVER, Decimal("1.09")),),
    ),
    DemoFixture(
        "en-avl-bha",
        "en-premier-league",
        "Aston Villa",
        "Brighton",
        2,
        "16:00",
        _p("0.45", "0.26", "0.29"),
        Decimal("0.60"),
    ),
    # La Liga
    DemoFixture(
        "es-rma-sev",
        "es-la-liga",
        "Real Madrid",
        "Sevilla",
        0,
        "22:00",
        _p("0.70", "0.17", "0.13"),
        Decimal("0.64"),
    ),
    DemoFixture(
        "es-bar-vil",
        "es-la-liga",
        "Barcelona",
        "Villarreal",
        1,
        "22:00",
        _p("0.66", "0.19", "0.15"),
        Decimal("0.68"),
        edges=(Edge(F, W2, Decimal("1.16")),),
    ),
    DemoFixture(
        "es-ath-rso",
        "es-la-liga",
        "Athletic Bilbao",
        "Real Sociedad",
        2,
        "20:00",
        _p("0.45", "0.29", "0.26"),
        Decimal("0.44"),
    ),
    # Serie A
    DemoFixture(
        "it-int-nap",
        "it-serie-a",
        "Inter",
        "Napoli",
        0,
        "21:45",
        _p("0.48", "0.27", "0.25"),
        Decimal("0.53"),
        edges=(Edge(D, H, Decimal("1.09")), Edge(B, X, Decimal("1.05"))),
    ),
    DemoFixture(
        "it-juv-rom",
        "it-serie-a",
        "Juventus",
        "Roma",
        1,
        "21:45",
        _p("0.47", "0.30", "0.23"),
        Decimal("0.47"),
    ),
    DemoFixture(
        "it-ata-laz",
        "it-serie-a",
        "Atalanta",
        "Lazio",
        2,
        "17:00",
        _p("0.50", "0.25", "0.25"),
        Decimal("0.61"),
    ),
    # Ligue 1
    DemoFixture(
        "fr-psg-om",
        "fr-ligue-1",
        "Paris Saint-Germain",
        "Marseille",
        0,
        "22:45",
        _p("0.64", "0.20", "0.16"),
        Decimal("0.67"),
    ),
    DemoFixture(
        "fr-lyo-mon",
        "fr-ligue-1",
        "Lyon",
        "Monaco",
        1,
        "18:00",
        _p("0.40", "0.26", "0.34"),
        Decimal("0.62"),
        edges=(Edge(E, H, Decimal("1.14")),),
    ),
    DemoFixture(
        "fr-lil-nic",
        "fr-ligue-1",
        "Lille",
        "Nice",
        2,
        "16:00",
        _p("0.46", "0.28", "0.26"),
        Decimal("0.50"),
    ),
    # Bundesliga
    DemoFixture(
        "de-fcb-bvb",
        "de-bundesliga",
        "Bayern München",
        "Borussia Dortmund",
        0,
        "20:30",
        _p("0.62", "0.19", "0.19"),
        Decimal("0.72"),
        edges=(Edge(B, W2, Decimal("1.14")), Edge(A, X, Decimal("1.06"))),
    ),
    DemoFixture(
        "de-b04-rbl",
        "de-bundesliga",
        "Bayer Leverkusen",
        "RB Leipzig",
        1,
        "17:30",
        _p("0.47", "0.24", "0.29"),
        Decimal("0.65"),
    ),
    DemoFixture(
        "de-vfb-sge",
        "de-bundesliga",
        "Stuttgart",
        "Eintracht Frankfurt",
        2,
        "19:30",
        _p("0.46", "0.24", "0.30"),
        Decimal("0.64"),
    ),
    # UEFA competitions
    DemoFixture(
        "ucl-rma-juv",
        "uefa-champions-league",
        "Real Madrid",
        "Juventus",
        3,
        "22:00",
        _p("0.56", "0.24", "0.20"),
        Decimal("0.58"),
    ),
    DemoFixture(
        "ucl-int-ben",
        "uefa-champions-league",
        "Inter",
        "Benfica",
        3,
        "22:00",
        _p("0.55", "0.25", "0.20"),
        Decimal("0.54"),
        edges=(Edge(F, W2, Decimal("1.17")),),
    ),
    DemoFixture(
        "uel-fb-por",
        "uefa-europa-league",
        "Fenerbahçe",
        "Porto",
        4,
        "19:45",
        _p("0.42", "0.28", "0.30"),
        Decimal("0.55"),
    ),
    DemoFixture(
        "uel-rom-aja",
        "uefa-europa-league",
        "Roma",
        "Ajax",
        4,
        "22:00",
        _p("0.51", "0.26", "0.23"),
        Decimal("0.56"),
    ),
    DemoFixture(
        "uecl-bjk-fio",
        "uefa-conference-league",
        "Beşiktaş",
        "Fiorentina",
        4,
        "19:45",
        _p("0.38", "0.28", "0.34"),
        Decimal("0.53"),
        edges=(Edge(E, X, Decimal("1.08")), Edge(B, H, Decimal("1.06"))),
    ),
    DemoFixture(
        "unl-tur-wal",
        "uefa-nations-league",
        "Türkiye",
        "Galler",
        5,
        "21:45",
        _p("0.55", "0.26", "0.19"),
        Decimal("0.48"),
    ),
    # EuroLeague basketball (two-way moneyline)
    DemoFixture(
        "el-fb-efes",
        "bask-euroleague",
        "Fenerbahçe Beko",
        "Anadolu Efes",
        0,
        "20:30",
        _p("0.62", "0.38"),
        sport=Sport.BASKETBALL,
        edges=(Edge(E, W2, Decimal("1.15")),),
    ),
    DemoFixture(
        "el-rma-oly",
        "bask-euroleague",
        "Real Madrid",
        "Olympiacos",
        1,
        "21:30",
        _p("0.55", "0.45"),
        sport=Sport.BASKETBALL,
    ),
)


# Competitions outside the tracked scope. They never reach the opportunity views,
# but the mapping admin shows how the taxonomy treats each kind of evidence.
@dataclass(frozen=True)
class UntrackedCompetition:
    source: Source
    competition_id: str
    name: str
    country: str | None
    home: str
    away: str
    derived_from_text: bool = False
    kind: CompetitionKind = CompetitionKind.LEAGUE


UNTRACKED: tuple[UntrackedCompetition, ...] = (
    # Structured provider ids in a known country map themselves to a canonical league.
    UntrackedCompetition(A, "7201", "1. Lig", "Türkiye", "Bandırmaspor", "Erzurumspor"),
    UntrackedCompetition(B, "910000000000000201", "Eredivisie", "Hollanda", "PSV", "Feyenoord"),
    UntrackedCompetition(E, "910000000000000202", "Liga Portugal", "Portekiz", "Sporting", "Braga"),
    UntrackedCompetition(F, "910000000000000203", "Pro League", "Belçika", "Club Brugge", "Gent"),
    # Text-only identities wait in the review queue, with a suggestion where one fits.
    UntrackedCompetition(
        D, "8202", "Süper Lig", "Türkiye", "Rizespor", "Alanyaspor", derived_from_text=True
    ),
    UntrackedCompetition(
        D,
        "8201",
        "Süper Kupa",
        None,
        "Galatasaray",
        "Beşiktaş",
        derived_from_text=True,
        kind=CompetitionKind.CUP,
    ),
    UntrackedCompetition(
        E,
        "910000000000000204",
        "Premier Lig 2",
        "İngiltere",
        "Chelsea U21",
        "Arsenal U21",
        derived_from_text=True,
    ),
    # A provider id whose country label does not resolve stays unmatched.
    UntrackedCompetition(
        A,
        "7202",
        "Kulüp Hazırlık Maçları",
        "Dünya",
        "Kocaelispor",
        "Sivasspor",
        kind=CompetitionKind.FRIENDLY,
    ),
)
