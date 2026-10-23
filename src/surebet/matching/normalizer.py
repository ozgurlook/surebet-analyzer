"""Normalization rules for team and competition names."""

import re
import unicodedata

_CHAR_MAP = str.maketrans(
    {
        # Turkish
        "ç": "c",
        "Ç": "c",
        "ğ": "g",
        "Ğ": "g",
        "ı": "i",
        "İ": "i",
        "ö": "o",
        "Ö": "o",
        "ş": "s",
        "Ş": "s",
        "ü": "u",
        "Ü": "u",
        # Nordic / Scandinavian
        "ø": "o",
        "Ø": "o",
        "æ": "ae",
        "Æ": "ae",
        "œ": "oe",
        "Œ": "oe",
        "å": "a",
        "Å": "a",
        # German
        "ß": "ss",
        # Slavic / Polish / Baltic
        "ł": "l",
        "Ł": "l",
        "đ": "d",
        "Đ": "d",
    }
)

_ABBREVIATIONS_PATTERN = re.compile(
    r"\b(a\.s\.|a\.s|a\.ş\.|a\.ş|aş|as|f\.c\.|f\.k\.|s\.k\.|j\.k\.|c\.f\.|u\.d\.|c\.d\.|s\.d\.)\b",
    re.IGNORECASE,
)

_EXPAND_PATTERNS = (
    (re.compile(r"\bst\.?\b", re.IGNORECASE), "saint"),
    (re.compile(r"\bdep\.?\b", re.IGNORECASE), "deportivo"),
    (re.compile(r"\butd\.?\b", re.IGNORECASE), "united"),
    (re.compile(r"\bwed\.?\b", re.IGNORECASE), "wednesday"),
    (re.compile(r"\bcumh\.?\b", re.IGNORECASE), "cumhuriyeti"),
    (re.compile(r"\buniv\.?\b", re.IGNORECASE), "universitatea"),
)

_STRIP_TOKENS = {
    "sk",
    "fk",
    "fc",
    "jk",
    "as",
    "kulubu",
    "kulub",
    "spor",
    "cf",
    "ac",
    "sc",
    "bsc",
    "sv",
    "vfl",
    "tsv",
    "vfb",
    "cd",
    "ud",
    "sd",
    "istanbul",
    "afc",
    "nfc",
    "bk",
    "if",
    "ff",
    "ik",
    "pfk",
    "sfk",
    "agf",
    "gf",
    "sportif",
    "faaliyetler",
}

_WORD_MAP = {
    # Munich
    "munih": "munich",
    "munchen": "munich",
    # Vienna
    "viyana": "vienna",
    "wien": "vienna",
    # Marseille
    "marsilya": "marseille",
    # Moscow
    "moskova": "moscow",
    # Bucharest
    "bukres": "bucuresti",
    # Athens
    "atina": "athens",
    # Sofia
    "sofya": "sofia",
    # Prague
    "prag": "prague",
    "praha": "prague",
    # Lausanne
    "lozan": "lausanne",
    # Strasbourg
    "strazburg": "strasbourg",
    # Lisbon
    "lizbon": "lisbon",
    # Dynamo / Dinamo
    "dynamo": "dinamo",
    # Direction & city tokens
    "bati": "west",
    "sehir": "city",
    "sehri": "city",
    "murettebati": "crew",
    "limani": "port",
    "kutukleri": "timbers",
    "guney": "south",
    # Countries & Cities
    "avusturya": "austria",
    "ermenistan": "armenia",
    "kreta": "crete",
    "kopenhag": "copenhagen",
    "kore": "korea",
    "kryliya": "krylia",
}

_PUNCTUATION_REGEX = re.compile(r"[^\w\s]")
_WHITESPACE_REGEX = re.compile(r"\s+")


def _ascii_fold(text: str) -> str:
    translated = text.translate(_CHAR_MAP)
    normalized = unicodedata.normalize("NFKD", translated)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    return ascii_only.lower()


_TEAM_ALIASES = {
    "inter milan": "inter",
    "internazionale": "inter",
    "ac milan": "milan",
    "paris sg": "paris saint germain",
    "psg": "paris saint germain",
    "man united": "manchester united",
    "man city": "manchester city",
    "qpr": "queens park rangers",
    "wolves": "wolverhampton",
    "wolverhampton wanderers": "wolverhampton",
    "crb": "club de regata brasil",
    "la galaxy": "los angeles galaxy",
    "ny red bulls": "new york red bulls",
    "sporting cp": "sporting",
    "sporting lizbon": "sporting",
    "sporting lisbon": "sporting",
    "sporting cp b": "sporting b",
    "sporting lizbon ii": "sporting b",
    "sporting lizbon 2": "sporting b",
    "oh leuven": "oud heverlee leuven",
    "leuven": "oud heverlee leuven",
    "kcca": "kampala city capital authority",
    "ucv": "universidad central de venezuela",
    "ucd": "uc dublin",
    "ham kam": "hamkam",
    "guney tirol": "sudtirol",
    "south tirol": "sudtirol",
    "stade rennes": "stade rennais",
    "rennes": "stade rennais",
    "olympiacos piraeus": "olympiakos",
    "olympiacos": "olympiakos",
    "olympiakos piraeus": "olympiakos",
    "rb salzburg": "red bull salzburg",
    "royale union sg": "royale union saint gilloise",
    "union saint gilloise": "royale union saint gilloise",
    "union sg": "royale union saint gilloise",
    # Basketball Euroleague
    "fenerbahce beko": "fenerbahce",
    "anadolu efes sk": "anadolu efes",
    "anadolu efes istanbul": "anadolu efes",
    "panathinaikos aktor athens": "panathinaikos",
    "panathinaikos aktor": "panathinaikos",
    "panathinaikos athens": "panathinaikos",
    "panathinaikos bc": "panathinaikos",
    "olympiacos piraeus bc": "olympiakos",
    "olympiakos piraeus bc": "olympiakos",
    "olympiacos bc": "olympiakos",
    "olympiakos bc": "olympiakos",
    "real madrid baloncesto": "real madrid",
    "real madrid basket": "real madrid",
    "fc barcelona basquet": "barcelona",
    "barcelona basquet": "barcelona",
    "ea7 emporio armani milan": "milano",
    "ea7 emporio armani milano": "milano",
    "olimpia milano": "milano",
    "armani milano": "milano",
    "kk partizan belgrade": "partizan",
    "partizan belgrade": "partizan",
    "kizilyildiz": "crvena zvezda",
    "laboral kutxa baskonia": "baskonia",
    "cazoo baskonia": "baskonia",
    "ldlc asvel villeurbanne": "asvel",
    "asvel lyon villeurbanne": "asvel",
    "maccabi playtika tel aviv": "maccabi tel aviv",
    "virtus segafredo bologna": "virtus bologna",
    "valencia basket": "valencia",
    "besiktas gain": "besiktas",
    "besiktas fibabanka": "besiktas",
    "besiktas emlakjet": "besiktas",
    "besiktas sompo japan": "besiktas",
    # National teams & International
    "cek cumhuriyeti": "cekya",
    "cek cumh": "cekya",
    "czech republic": "cekya",
    "czechia": "cekya",
    "kosovo": "kosova",
    "kosova cumhuriyeti": "kosova",
    "republic of kosovo": "kosova",
    "beyaz rusya": "belarus",
    "luxembourg": "luksemburg",
    "liechtenstein": "lihtenstayn",
    "kore cumhuriyeti": "guney kore",
    "korea cumhuriyeti": "guney kore",
    "south korea": "guney kore",
    "south kore": "guney kore",
    "irlanda cumhuriyeti": "irlanda",
    "republic of ireland": "irlanda",
    "kuzey irlanda": "kuzey irlanda",
    "northern ireland": "kuzey irlanda",
    "kuzey makedonya": "makedonya",
    "north macedonia": "makedonya",
    "bosna hersek": "bosna hersek",
    "bosna ve hersek": "bosna hersek",
    "bosnia and herzegovina": "bosna hersek",
    "suudi arabistan": "suudi arabistan",
    "saudi arabia": "suudi arabistan",
    "birlesik arap emirlikleri": "bae",
    "uae": "bae",
    "fildisi sahili": "fildisi sahili",
    "ivory coast": "fildisi sahili",
    "cote d ivoire": "fildisi sahili",
    "demokratik kongo cumhuriyeti": "kongo dc",
    "dr congo": "kongo dc",
    "yesil burun adalari": "cape verde",
    "cabo verde": "cape verde",
    # Turkish League & Clubs
    "amedspor sk": "amed",
    "amed sfk": "amed",
    "amed sportif faaliyetler": "amed",
    "amedspor": "amed",
    # European Clubs
    "kizil yildiz belgrade": "crvena zvezda",
    "kizil yildiz": "crvena zvezda",
    "crvena zvezda belgrade": "crvena zvezda",
    "red star belgrade": "crvena zvezda",
    "red star": "crvena zvezda",
    "univ craiova": "universitatea craiova",
    "cs u craiova": "universitatea craiova",
    "u craiova": "universitatea craiova",
    "kf egnatia": "egnatia",
    "fk egnatia rrogozhine": "egnatia",
    "egnatia rrogozhine": "egnatia",
    "ic d escaldes": "inter d escaldes",
    "inter club d escaldes": "inter d escaldes",
    "inter club escaldes": "inter d escaldes",
    "fc kopenhag": "copenhagen",
    "kopenhag": "copenhagen",
    "sporting braga": "braga",
    "sint truidense": "sint truiden",
    "fc saburtalo tiflis": "iberia 1999",
    "saburtalo tiflis": "iberia 1999",
    "saburtalo tbilisi": "iberia 1999",
    "saburtalo": "iberia 1999",
    "spyris kaunas": "kauno zalgiris",
    "fk spyris kaunas": "kauno zalgiris",
    "zalgiris kauno": "kauno zalgiris",
    "heart of midlothian": "hearts",
    "agf aarhus": "aarhus",
    "aarhus gf": "aarhus",
    "ofi kreta": "ofi crete",
    "ararat ermenistan": "ararat armenia",
    "fc ararat armenia": "ararat armenia",
    "kryliya sovetov": "krylia sovetov",
    "krylia sovetov samara": "krylia sovetov",
}


def normalize_team_name(name: str) -> str:
    """Normalize a football team name for conservative cross-source matching."""
    text = _ascii_fold(name)
    # Remove parenthesized or bracketed content like (PSG), [W], (Altyapı), etc.
    text = re.sub(r"\(.*?\)", "", text)
    text = re.sub(r"\[.*?\]", "", text)
    # Strip dotted abbreviations first before removing punctuation
    text = _ABBREVIATIONS_PATTERN.sub(" ", text)
    # Expand common abbreviations (st -> saint, dep -> deportivo, utd -> united, wed -> wednesday)
    for pattern, replacement in _EXPAND_PATTERNS:
        text = pattern.sub(replacement, text)
    # Remove punctuation
    text = _PUNCTUATION_REGEX.sub(" ", text)
    tokens = text.split()
    # Apply word/city translation map
    mapped_tokens = [_WORD_MAP.get(token, token) for token in tokens]
    filtered_tokens = [
        token for token in mapped_tokens if token not in _STRIP_TOKENS and len(token) > 0
    ]

    result = " ".join(filtered_tokens if filtered_tokens else tokens)
    cleaned = _WHITESPACE_REGEX.sub(" ", result).strip()
    return _TEAM_ALIASES.get(cleaned, cleaned)


def normalize_competition(name: str) -> str:
    """Normalize league or tournament name."""
    text = _ascii_fold(name)
    # Remove country prefixes like "Türkiye - ", "İngiltere - ", "Turkey: ", etc.
    text = re.sub(r"^.*?[-:]\s*", "", text)
    # Remove sponsor prefixes like "Trendyol ", "Barclays ", "LaLiga EA Sports"
    text = re.sub(r"\b(trendyol|barclays|ea sports|santander)\b", "", text)
    text = _PUNCTUATION_REGEX.sub(" ", text)
    text = _WHITESPACE_REGEX.sub(" ", text).strip()

    # Canonical aliases
    if "super lig" in text or "superlig" in text:
        return "super lig"
    if "champions league" in text or "sampiyonlar ligi" in text:
        return "uefa champions league"
    if "europa league" in text or "avrupa ligi" in text:
        return "uefa europa league"
    if "conference league" in text or "konferans ligi" in text:
        return "uefa conference league"
    if "premier league" in text or "premier lig" in text:
        return "premier league"
    if "la liga" in text or "laliga" in text or "primera division" in text:
        return "la liga"
    if "serie a" in text:
        return "serie a"
    if "bundesliga" in text:
        return "bundesliga"
    if "ligue 1" in text:
        return "ligue 1"

    return text
