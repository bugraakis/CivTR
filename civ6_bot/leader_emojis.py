# leader_emojis.py
# Lider adı → Discord sunucu emojisinin ADI (ID gerekmez; bot runtime'da guild'den çeker).
# Bot, discord.utils.get(guild.emojis, name=...) ile tam <:name:id> formatını alır.
# Emoji yüklenmemişse veya bulunamazsa boş string döner.

LEADER_EMOJI_NAMES: dict[str, str | None] = {
    # America
    "Abraham Lincoln":                    "AbrahamLincoln",
    "Teddy Roosevelt (Bull Moose)":       "TeddyBMAmerica",
    "Teddy Roosevelt (Rough Rider)":      "TeddyRRAmerica",
    # Arabia
    "Saladin (Vizier)":                   "Saladin",
    "Saladin (Sultan)":                   "SultanSaladin",
    # Australia
    "John Curtin":                        "JohnCurtin",
    # Aztec
    "Montezuma":                          "MontezumaAztec",
    # Babylon
    "Hammurabi":                          "Hammurabi",
    # Brazil
    "Pedro II":                           "Pedro",
    # Byzantium
    "Basil II":                           "Basil",
    "Theodora":                           "Theodora",
    # Canada
    "Wilfrid Laurier":                    "WilfridLaurier",
    # China
    "Kublai Khan (China)":                "KublaiKhanChina",
    "Qin (Mandate of Heaven)":            "QinShiHuang",
    "Qin (Unifier)":                      "QinUnifier",
    "Wu Zetian":                          "WunZetian",
    "Yongle":                             "Yongle",
    # Cree
    "Poundmaker":                         "PoundmakerCree",
    # Egypt
    "Cleopatra (Egyptian)":               "Cleopatra",
    "Cleopatra (Ptolemaic)":              "PtolemicCleopatra",
    "Ramses II":                          "Ramses",
    # England
    "Eleanor of Aquitaine (England)":     "ElanorEngland",
    "Elizabeth I":                        "ElizabethI",
    "Victoria (Age of Empire)":           "Victoria",
    "Victoria (Age of Steam)":            "Victoria",
    # Ethiopia
    "Menelik II":                         "Menelik",
    # France
    "Catherine de Medici (Black Queen)":  "CatherineBlackQueen",
    "Catherine de Medici (Magnificence)": "CatherineMagnificient",
    "Eleanor of Aquitaine (France)":      "ElanorFrance",
    # Gaul
    "Ambiorix":                           "Ambiorix",
    "Vercingetorix":                      "Vercingetorix",
    # Georgia
    "Tamar":                              "Tamar",
    # Germany
    "Frederick Barbarossa":               "FrederickBarbarossa",
    "Ludwig II":                          "LudwigII",
    # Gran Colombia
    "Simón Bolívar":                      "SimonBolivar",
    # Greece
    "Gorgo":                              "Gorgo",
    "Pericles":                           "Pericles",
    # Hungary
    "Matthias Corvinus":                  "MatthiasCorvinus",
    # Inca
    "Pachacuti":                          "Pachacuti",
    # India
    "Chandragupta":                       "Chandragupta",
    "Gandhi":                             "Gandhi",
    # Indonesia
    "Gitarja":                            "Gitarja",
    # Japan
    "Hojo Tokimune":                      "HojoTokimune",
    "Tokugawa":                           "Tokugawa",
    # Khmer
    "Jayavarman VII":                     "Jayavarman",
    # Kongo
    "Mvemba a Nzinga":                    "MvembaaNzinga",
    "Nzinga Mbande":                      "QueenMbandeNzinga",
    # Korea
    "Sejong":                             "Sejong",
    "Seondeok":                           "Seondeok",
    # Macedon
    "Alexander":                          "Alexander",
    "Olympias":                           "Olympias",
    # Mali
    "Mansa Musa":                         "MansaMusa",
    "Sundiata Keita":                     "SundeitaKeitaMali",
    # Māori
    "Kupe":                               "Kupe",
    # Mapuche
    "Lautaro":                            "Lautaro",
    # Maya
    "Lady Six Sky":                       "LadySixSkyMaya",
    "Te' K'inich II":                     "TeKinichII",
    # Mongolia
    "Genghis Khan":                       "GenghisKhan",
    "Kublai Khan (Mongolia)":             "KublaiKhanMongolia",
    # Netherlands
    "Wilhelmina":                         "Wilhelmina",
    # Norway
    "Harald Hardrada (Varangian)":        "HaraldHardrada",
    "Harald Hardrada (Konge)":            "HaraldHardrada",
    # Nubia
    "Amanitore":                          "Amanitore",
    # Ottomans
    "Suleiman (Kanuni)":                  "SuleimanKanuni",
    "Suleiman (Muhteşem)":                "SuleimanMuhtesemn",
    # Persia
    "Cyrus":                              "Cyrus",
    "Nader Shah":                         "NaderShah",
    # Phoenicia
    "Dido":                               "Dido",
    "Ahiram":                             "Ahiram",
    # Poland
    "Jadwiga":                            "Jadwiga",
    # Portugal
    "João III":                           "JoaoIIIPortugal",
    # Rome
    "Julius Caesar":                      "JuliusCaesar",
    "Trajan":                             "Trajan",
    # Russia
    "Peter":                              "Peter",
    # Scotland
    "Robert the Bruce":                   "RoberttheBruce",
    # Scythia
    "Tomyris":                            "Tomyris",
    # Spain
    "Philip II":                          "Philip",
    # Sumeria
    "Gilgamesh":                          "Gilgamesh",
    # Swahili
    "Al-Hasan ibn Sulaiman":              "AlHasanibnSulaiman",
    # Sweden
    "Kristina":                           "Kristina",
    # Teotihuacán
    "Spearthrower Owl":                   "SpearthrowerOwl",
    # Thule
    "Kiviuq":                             "Kiviuq",
    # Tibet
    "Trisong Detsen":                     "TrisongDetsen",
    # Vietnam
    "Bà Triệu":                           "BaTrieu",
    # Zulu
    "Shaka":                              "Shaka",
}
