from surebet.matching.normalizer import normalize_competition, normalize_team_name


def test_normalize_team_name() -> None:
    assert normalize_team_name("Fenerbahçe SK") == "fenerbahce"
    assert normalize_team_name("Galatasaray A.Ş.") == "galatasaray"
    assert normalize_team_name("Besiktas JK") == "besiktas"
    # Munich variations
    assert normalize_team_name("FC Bayern München") == "bayern munich"
    assert normalize_team_name("FC Bayern Münih") == "bayern munich"
    assert normalize_team_name("Bayern Munich") == "bayern munich"
    # PSG variations
    assert normalize_team_name("Paris Saint-Germain (PSG)") == "paris saint germain"
    assert normalize_team_name("Paris St-Germain") == "paris saint germain"
    assert normalize_team_name("PSG") == "paris saint germain"
    # Monaco variations
    assert normalize_team_name("AS Monaco") == "monaco"
    assert normalize_team_name("Monaco") == "monaco"
    # Deportivo variations
    assert normalize_team_name("Dep La Coruna") == "deportivo la coruna"
    assert normalize_team_name("Deportivo de A Coruna") == "deportivo de a coruna"
    # European / Scandinavian character preservation
    assert normalize_team_name("Lillestrøm") == "lillestrom"
    assert normalize_team_name("SønderjyskE") == "sonderjyske"
    assert normalize_team_name("SC Preußen Münster") == "preussen munster"
    assert normalize_team_name("Jagiellonia Białystok") == "jagiellonia bialystok"
    # City and language translations
    assert normalize_team_name("Marseille") == "marseille"
    assert normalize_team_name("Marsilya") == "marseille"
    assert normalize_team_name("Rapid Wien") == "rapid vienna"
    assert normalize_team_name("Rapid Viyana") == "rapid vienna"
    assert normalize_team_name("FK Austria Wien") == "austria vienna"
    assert normalize_team_name("Avusturya Viyana") == "austria vienna"
    assert normalize_team_name("Spartak Moscow") == "spartak moscow"
    assert normalize_team_name("Spartak Moskova") == "spartak moscow"
    assert normalize_team_name("Dinamo Bucuresti") == "dinamo bucuresti"
    assert normalize_team_name("Dinamo Bükreş") == "dinamo bucuresti"
    assert normalize_team_name("AEK Athens") == "aek athens"
    assert normalize_team_name("AEK Atina") == "aek athens"
    assert normalize_team_name("Levski Sofia") == "levski sofia"
    assert normalize_team_name("Levski Sofya") == "levski sofia"
    assert normalize_team_name("Slavia Praha") == "slavia prague"
    assert normalize_team_name("Slavia Prag") == "slavia prague"
    assert normalize_team_name("Lausanne") == "lausanne"
    assert normalize_team_name("Lozan") == "lausanne"
    assert normalize_team_name("FC Sudtirol") == "sudtirol"
    assert normalize_team_name("Güney Tirol") == "sudtirol"
    assert normalize_team_name("Sporting CP") == "sporting"
    assert normalize_team_name("Sporting Lizbon") == "sporting"
    assert normalize_team_name("West Bromwich Albion") == "west bromwich albion"
    assert normalize_team_name("Batı Brom") == "west brom"
    assert normalize_team_name("Bristol City") == "bristol city"
    assert normalize_team_name("Bristol Şehir") == "bristol city"
    assert normalize_team_name("Columbus Crew") == "columbus crew"
    assert normalize_team_name("Columbus Mürettebatı") == "columbus crew"
    # Acronyms & common contractions
    assert normalize_team_name("QPR") == "queens park rangers"
    assert normalize_team_name("Wolverhampton Wanderers") == "wolverhampton"
    assert normalize_team_name("Wolves") == "wolverhampton"
    assert normalize_team_name("CRB") == "club de regata brasil"
    assert normalize_team_name("St. Johnstone FC") == "saint johnstone"
    assert normalize_team_name("Sheffield United") == "sheffield united"
    assert normalize_team_name("Sheffield Utd") == "sheffield united"
    assert normalize_team_name("Dundee United") == "dundee united"
    assert normalize_team_name("Dundee Utd") == "dundee united"
    # Standard clubs
    assert normalize_team_name("Real Madrid CF") == "real madrid"
    assert normalize_team_name("Inter Milan") == "inter"
    assert normalize_team_name("AC Milan") == "milan"
    assert normalize_team_name("Trabzonspor Kulübü") == "trabzonspor"
    # European club variations
    assert normalize_team_name("Olympiacos Piraeus") == "olympiakos"
    assert normalize_team_name("Olympiakos") == "olympiakos"


def test_normalize_euroleague_basketball_teams() -> None:
    assert normalize_team_name("Fenerbahçe Beko") == "fenerbahce"
    assert normalize_team_name("Fenerbahce Istanbul") == "fenerbahce"
    assert normalize_team_name("Anadolu Efes SK") == "anadolu efes"
    assert normalize_team_name("Panathinaikos AKTOR Athens") == "panathinaikos"
    assert normalize_team_name("Olympiacos Piraeus BC") == "olympiakos"
    assert normalize_team_name("Real Madrid Baloncesto") == "real madrid"
    assert normalize_team_name("FC Barcelona Basquet") == "barcelona"
    assert normalize_team_name("EA7 Emporio Armani Milan") == "milano"
    assert normalize_team_name("KK Partizan Belgrade") == "partizan"
    assert normalize_team_name("Kızılyıldız") == "crvena zvezda"
    assert normalize_team_name("BC Dubai") == "bc dubai"


def test_normalize_competition() -> None:
    assert normalize_competition("Süper Lig") == "super lig"
    assert normalize_competition("Trendyol Süper Lig") == "super lig"
    assert normalize_competition("Türkiye - Süper Lig") == "super lig"
    assert normalize_competition("UEFA Şampiyonlar Ligi") == "uefa champions league"
    assert normalize_competition("UEFA Champions League") == "uefa champions league"
    assert normalize_competition("Premier League") == "premier league"
    assert normalize_competition("İngiltere - Premier League") == "premier league"


def test_normalize_national_teams_and_tracked_clubs() -> None:
    # Czechia
    czech_norm = normalize_team_name("Çekya")
    assert normalize_team_name("Çek Cumhuriyeti") == czech_norm
    assert normalize_team_name("Çek Cumh.") == czech_norm
    assert normalize_team_name("Czech Republic") == czech_norm
    assert normalize_team_name("Czechia") == czech_norm

    # Kosovo
    kosovo_norm = normalize_team_name("Kosova")
    assert normalize_team_name("Kosovo") == kosovo_norm
    assert normalize_team_name("Kosova Cumhuriyeti") == kosovo_norm
    assert normalize_team_name("Republic of Kosovo") == kosovo_norm

    # Belarus
    assert normalize_team_name("Beyaz Rusya") == normalize_team_name("Belarus")

    # Luxembourg & Liechtenstein
    assert normalize_team_name("Luxembourg") == normalize_team_name("Lüksemburg")
    assert normalize_team_name("Liechtenstein") == normalize_team_name("Lihtenştayn")

    # South Korea & Others
    assert normalize_team_name("Güney Kore") == normalize_team_name("Kore Cumhuriyeti")
    assert normalize_team_name("İrlanda Cumhuriyeti") == normalize_team_name("İrlanda")
    assert normalize_team_name("Kuzey İrlanda") == normalize_team_name("Northern Ireland")
    assert normalize_team_name("Kuzey Makedonya") == normalize_team_name("Makedonya")
    assert normalize_team_name("Bosna Hersek") == normalize_team_name("Bosna ve Hersek")

    # Basketball Euroleague Sponsors
    assert normalize_team_name("Beşiktaş Gain") == "besiktas"
    assert normalize_team_name("Beşiktaş Fibabanka") == "besiktas"
    assert normalize_team_name("Beşiktaş Emlakjet") == "besiktas"

    # Turkish League Suffixes
    assert normalize_team_name("Amedspor SK") == "amed"
    assert normalize_team_name("Amed SFK") == "amed"
    assert normalize_team_name("Amed Sportif Faaliyetler") == "amed"

    # European Clubs
    assert normalize_team_name("Kızıl Yıldız Belgrade") == "crvena zvezda"
    assert normalize_team_name("Kızıl Yıldız") == "crvena zvezda"
    assert normalize_team_name("Red Star Belgrade") == "crvena zvezda"
    assert normalize_team_name("Universitatea Craiova") == normalize_team_name("Univ. Craiova")
    assert normalize_team_name("Universitatea Craiova") == normalize_team_name("CS U Craiova")
    assert normalize_team_name("KF Egnatia") == normalize_team_name("FK Egnatia Rrogozhine")
    assert normalize_team_name("IC d'Escaldes") == normalize_team_name("Inter Club d'Escaldes")
    assert normalize_team_name("IC d'Escaldes") == normalize_team_name("Inter d'Escaldes")
    assert normalize_team_name("FC Kopenhag") == normalize_team_name("FC Copenhagen")
    assert normalize_team_name("FC Saburtalo Tiflis") == normalize_team_name("Iberia 1999")
    assert normalize_team_name("Fk Spyris Kaunas") == normalize_team_name("Kauno Zalgiris")
    assert normalize_team_name("Zalgiris Kauno") == normalize_team_name("Kauno Zalgiris")
    assert normalize_team_name("Heart of Midlothian") == normalize_team_name("Hearts")
    assert normalize_team_name("AGF Aarhus") == normalize_team_name("Aarhus GF")
    assert normalize_team_name("OFI Crete FC") == normalize_team_name("Ofi Kreta")
    assert normalize_team_name("FC Ararat-Armenia") == normalize_team_name("Ararat-Ermenistan")
    assert normalize_team_name("Krylia Sovetov Samara") == normalize_team_name("Kryliya Sovetov")
