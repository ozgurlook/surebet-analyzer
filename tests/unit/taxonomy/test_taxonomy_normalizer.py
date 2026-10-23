from surebet.taxonomy.models import RegionClass
from surebet.taxonomy.normalizer import CountryDictionary, normalize_taxonomy_text, turkish_sort_key


def test_normalize_taxonomy_text_is_unicode_and_punctuation_safe() -> None:
    assert (
        normalize_taxonomy_text("  S\u00dcPER\uff0fLig\u2014Kad\u0131nlar  ")
        == "s\u00fcper lig kad\u0131nlar"
    )


def test_turkish_sort_key_orders_dotted_letters_after_their_base_letter() -> None:
    # \u00c7/\u00d6/\u015e/\u00dc/\u011e each rank strictly between their base letter and the next plain letter,
    # unlike plain codepoint order which pushes them all after "z".
    assert turkish_sort_key("C") < turkish_sort_key("\u00c7") < turkish_sort_key("D")
    assert turkish_sort_key("O") < turkish_sort_key("\u00d6") < turkish_sort_key("P")
    assert turkish_sort_key("S") < turkish_sort_key("\u015e") < turkish_sort_key("T")
    assert turkish_sort_key("U") < turkish_sort_key("\u00dc") < turkish_sort_key("V")
    assert turkish_sort_key("G") < turkish_sort_key("\u011e") < turkish_sort_key("H")


def test_turkish_sort_key_distinguishes_dotless_and_dotted_i() -> None:
    # Both sit between H and J, dotless "I"/"\u0131" strictly before dotted "\u0130"/"i".
    assert (
        turkish_sort_key("H")
        < turkish_sort_key("I")
        < turkish_sort_key("\u0130")
        < turkish_sort_key("J")
    )
    # Case folding must not collapse the two forms onto each other.
    assert turkish_sort_key("\u0131") == turkish_sort_key("I")
    assert turkish_sort_key("i") == turkish_sort_key("\u0130")
    assert turkish_sort_key("I") != turkish_sort_key("\u0130")


def test_turkish_sort_key_is_case_insensitive_per_letter() -> None:
    assert turkish_sort_key("\u00e7ekya") == turkish_sort_key("\u00c7EKYA")
    assert turkish_sort_key("\u00c7ekya") == turkish_sort_key("\u00e7ekya")


def test_turkish_sort_key_places_full_words_in_turkish_alphabetical_order() -> None:
    words = ["Zimbabve", "\u00c7ekya", "Cezayir", "Danimarka"]
    assert sorted(words, key=turkish_sort_key) == ["Cezayir", "\u00c7ekya", "Danimarka", "Zimbabve"]


def test_country_aliases_resolve_without_network() -> None:
    dictionary = CountryDictionary.load_packaged()

    assert dictionary.resolve("Turkey").iso_code == "TR"
    assert dictionary.resolve("T\u00fcrkiye").display_name == "T\u00fcrkiye"
    assert dictionary.resolve("T\u00dcRK\u0130YE").iso_code == "TR"
    assert dictionary.resolve("TR").region_id == "country:TR"
    assert dictionary.resolve("Germany").iso_code == "DE"
    assert dictionary.resolve("Almanya").iso_code == "DE"
    assert dictionary.resolve("DE").display_name == "Almanya"
    assert dictionary.resolve("not-a-country") is None


def test_special_regions_are_not_iso_countries() -> None:
    dictionary = CountryDictionary.load_packaged()

    international = dictionary.resolve("International")
    unknown = dictionary.resolve("Unknown")

    assert international is not None
    assert international.region_id == "region:international"
    assert international.region_class is RegionClass.INTERNATIONAL
    assert international.iso_code is None
    assert unknown is not None
    assert unknown.region_id == "region:unknown"
    assert unknown.region_class is RegionClass.UNKNOWN
    assert unknown.iso_code is None


def test_packaged_dictionary_is_versioned_and_complete() -> None:
    dictionary = CountryDictionary.load_packaged()

    assert dictionary.version == "2026-08-26"
    assert len(dictionary.countries) == 249
    assert len({country.iso_code for country in dictionary.countries}) == 249
    assert all(country.display_name for country in dictionary.countries)
    assert all(
        dictionary.resolve(country.iso_code or "") == country for country in dictionary.countries
    )
