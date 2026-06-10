import csv
import random

PERSONALITY_TYPES = {
    "metropolen_fan": {
        "name": "Der Metropolen-Fan",
        "emoji": "🏙️",
        "tagline": "Du liebst das pulsierende Großstadtleben",
        "desc": "Skylines, Nachtleben und das Summen einer Millionenstadt – du fühlst dich in riesigen Städten am lebendigsten. Je größer, desto besser.",
    },
    "strandliebhaber": {
        "name": "Der Strandliebhaber",
        "emoji": "🏖️",
        "tagline": "Sand, Sonne und Meer – mehr brauchst du nicht",
        "desc": "Dein perfekter Urlaub beginnt und endet am Wasser. Warme Temperaturen und das Rauschen der Wellen sind für dich absolute Pflicht.",
    },
    "kulturreisender": {
        "name": "Der Kulturreisende",
        "emoji": "🏛️",
        "tagline": "Du reist für Geschichte, Kunst und Atmosphäre",
        "desc": "Museen, alte Gassen, lokale Küche – du willst nicht nur irgendwo sein, sondern wirklich etwas erleben und verstehen.",
    },
    "backpacker": {
        "name": "Der Backpacker",
        "emoji": "🎒",
        "tagline": "Weit reisen, wenig ausgeben",
        "desc": "Budget? Klein. Entdeckergeist? Riesig. Du weißt, dass die besten Reiseerlebnisse nicht die teuersten sein müssen.",
    },
    "luxusurlauber": {
        "name": "Der Luxusurlauber",
        "emoji": "✈️",
        "tagline": "Reisen auf höchstem Niveau",
        "desc": "Für dich ist Urlaub keine Frage des Preises, sondern der Qualität. Du wählst Städte, die das Beste bieten – und gibst es gern aus.",
    },
    "entdecker": {
        "name": "Der Entdecker",
        "emoji": "🧭",
        "tagline": "Abseits der ausgetretenen Pfade",
        "desc": "Touristenfallen? Nichts für dich. Du suchst das Unbekannte, die kleine Stadt, den unerwarteten Ort – und findest dort das Besondere.",
    },
}


# Anteil jeder Kategorie im Städtedatensatz (190 Städte, gemessen).
# Dient zur Prior-Korrektur: nur Präferenzen *über* der Basisrate zählen.
_PRIOR_COST  = [0.032, 0.316, 0.332, 0.321]  # <20, 20-40, 40-60, 60+
_PRIOR_POP   = [0.084, 0.216, 0.263, 0.437]  # <100k, 100k-500k, 500k-1.7M, 1.7M+
_PRIOR_OCEAN = [0.116, 0.321, 0.563]          # <5km, 5-50km, >50km
_PRIOR_LAT   = [0.379, 0.600, 0.021]          # <30°, 30-60°, >60°


def _personality_scores(user):
    def excess(liked, disliked, prior):
        """Wie viel *mehr* als die Basisrate mag der Nutzer diese Kategorie?"""
        net = [max(0, liked[i] - disliked[i]) for i in range(len(liked))]
        total = sum(net)
        if total == 0:
            return [0.0] * len(liked)
        probs = [x / total for x in net]
        return [max(0.0, probs[i] - prior[i]) for i in range(len(liked))]

    lp = excess(user["count_liked_population"],     user["count_disliked_population"],     _PRIOR_POP)
    lc = excess(user["count_liked_cost"],           user["count_disliked_cost"],           _PRIOR_COST)
    lo = excess(user["count_liked_ocean_distance"], user["count_disliked_ocean_distance"], _PRIOR_OCEAN)
    ll = excess(user["count_liked_abs_latitude"],   user["count_disliked_abs_latitude"],   _PRIOR_LAT)

    return {
        "metropolen_fan":  lp[3] * 5,
        "strandliebhaber": lo[0] * 4 + ll[0] * 2,
        "kulturreisender": ll[1] * 2 + (lp[1] + lp[2]) * 1.5 + lc[1] * 1,
        "backpacker":      lc[0] * 3 + lc[1] * 2,
        "luxusurlauber":   lc[3] * 4 + lp[3] * 1,
        "entdecker":       ll[2] * 2 + lp[0] * 2 + lo[2] * 1,
    }


def _fallback_scores(user):
    """Net-Norm-Scores ohne Prior-Korrektur – nur als Tiebreaker bei Null-Signal."""
    def net_norm(liked, disliked):
        net = [max(0, liked[i] - disliked[i]) for i in range(len(liked))]
        total = sum(net)
        if total == 0:
            return [1 / len(liked)] * len(liked)
        return [x / total for x in net]

    lp = net_norm(user["count_liked_population"],     user["count_disliked_population"])
    lc = net_norm(user["count_liked_cost"],           user["count_disliked_cost"])
    lo = net_norm(user["count_liked_ocean_distance"], user["count_disliked_ocean_distance"])
    ll = net_norm(user["count_liked_abs_latitude"],   user["count_disliked_abs_latitude"])

    coast_over_inland = max(0, lo[0] - lo[2])
    warm_over_cold    = max(0, ll[0] - ll[2])

    return {
        "metropolen_fan":  lp[3] * 5,
        "strandliebhaber": coast_over_inland * (4 + warm_over_cold),
        "kulturreisender": ll[1] * 2 + (lp[1] + lp[2]) * 1.5 + lc[1] * 1,
        "backpacker":      lc[0] * 3 + lc[1] * 2,
        "luxusurlauber":   lc[3] * 4 + lp[3] * 1,
        "entdecker":       ll[2] * 2 + lp[0] * 2 + lo[2] * 1,
    }


def _best_key(user):
    scores = _personality_scores(user)
    if any(v > 0 for v in scores.values()):
        return max(scores, key=scores.get)
    return max(_fallback_scores(user), key=_fallback_scores(user).get)


def get_personality_type(user):
    return PERSONALITY_TYPES[_best_key(user)]


def get_personality_key(user):
    return _best_key(user)


def get_todo_activities(user, cities):
    """Wählt 5 passende Aktivitäten basierend auf der Top-Stadt und dem Nutzerprofil."""
    if not cities:
        return []

    top = cities[0]

    continent      = top[2] if len(top) > 2 else ""
    country        = top[1] if len(top) > 1 else ""
    cost_raw       = top[4] if len(top) > 4 and top[4] != '' else "40"
    population_raw = top[6] if len(top) > 6 else "500000"
    ocean_raw      = top[7] if len(top) > 7 else "100"

    cost_cat  = categorise([20, 40, 60],              float(cost_raw))
    pop_cat   = categorise([100000, 500000, 1700000], float(population_raw))
    ocean_cat = categorise([5, 50],                   float(ocean_raw))

    personality_key = get_personality_key(user)

    # Bedingungen
    outside_europe = continent != "Europa"
    is_kultur      = personality_key == "kulturreisender"
    is_high_price  = cost_cat >= 2   # Gehoben oder Luxuriös
    is_low_price   = cost_cat <= 1   # Günstig oder Erschwinglich
    is_ireland     = country == "Irland"
    is_small_city  = pop_cat == 0    # Kleinstadt
    near_sea       = ocean_cat <= 1  # Direkt am Meer oder Küstennah

    specific = []
    sleeping_under_stars = False

    if outside_europe:
        specific.append("Probiere 3 neue lokale Spezialitäten")
    if is_kultur:
        specific.append("Besuche die Nationale Kunstgalerie")
    if is_high_price:
        specific.append("Probiere einen neuen Abenteuersport aus")
    if is_low_price:
        specific.append("Stelle dir ein 10€-Outfit in einem Second-Hand-Laden zusammen")
    if is_ireland:
        specific.append("Trinke ein Guinness in einem Pub")
    if is_small_city:
        specific.append("Schlafe unter den Sternen")
        sleeping_under_stars = True
    if near_sea:
        specific.append("Springe von einer Klippe ins Wasser")
        if not sleeping_under_stars:
            specific.append("Schlafe am Strand")

    universal = [
        "Stehe früh auf und sieh den Sonnenaufgang",
        "Leihe dir einen Motorroller aus und erkunde das Umland",
        "Mache eine Fototour zu einem bestimmten Thema (Farben, Schatten, Türen, …)",
        "Filme deinen Urlaub wie eine Dokumentation",
        "Schreibe eine Postkarte an deine Freunde und Familie",
        "Singe Karaoke in einer anderen Sprache",
    ]

    random.shuffle(specific)
    result = specific[:5]

    if len(result) < 5:
        available = [u for u in universal if u not in result]
        random.shuffle(available)
        result += available[:5 - len(result)]

    return result


# Labels für die Bins jeder Kategorie
_COST_LABELS  = ["Günstig", "Erschwinglich", "Gehoben", "Luxuriös"]
_POP_LABELS   = ["Kleinstadt", "Mittelgroß", "Großstadt", "Megacity"]
_OCEAN_LABELS = ["Direkt am Meer", "Küstennah", "Im Landesinneren"]
_LAT_LABELS   = ["Tropisch", "Gemäßigt", "Kühl & Polar"]


def _get_global_bin_distributions(liked_cities, city_list):
    """Berechnet die globale Bin-Verteilung aller Likes aus der DB."""
    cost_cats  = [20, 40, 60]
    pop_cats   = [100000, 500000, 1700000]
    ocean_cats = [5, 50]
    lat_cats   = [30, 60]

    city_map = {city[0]: city for city in city_list}

    g_cost  = [0, 0, 0, 0]
    g_pop   = [0, 0, 0, 0]
    g_ocean = [0, 0, 0]
    g_lat   = [0, 0, 0]

    for city_name in liked_cities:
        city = city_map.get(city_name)
        if not city:
            continue
        if city[4] != '':
            g_cost[categorise(cost_cats, city[4])] += 1
        g_pop[categorise(pop_cats, city[6])] += 1
        g_ocean[categorise(ocean_cats, city[7])] += 1
        g_lat[categorise(lat_cats, city[8])] += 1

    def to_pct(lst):
        total = sum(lst)
        if total == 0:
            return [round(100 / len(lst))] * len(lst)
        return [round(x / total * 100) for x in lst]

    return {
        "cost":       to_pct(g_cost),
        "population": to_pct(g_pop),
        "ocean":      to_pct(g_ocean),
        "latitude":   to_pct(g_lat),
    }


def calculate_rarity(user, city_list, liked_cities_global):
    """Vergleicht die Präferenzen des Users mit allen anderen Spielern."""

    def dominant(lst):
        return lst.index(max(lst)) if max(lst) > 0 else 0

    dom_cost  = dominant(user["count_liked_cost"])
    dom_pop   = dominant(user["count_liked_population"])
    dom_ocean = dominant(user["count_liked_ocean_distance"])
    dom_lat   = dominant(user["count_liked_abs_latitude"])

    dist = _get_global_bin_distributions(liked_cities_global, city_list)

    comparisons = [
        {
            "category": "Lebenskosten",
            "user_pref": _COST_LABELS[dom_cost],
            "pct": dist["cost"][dom_cost],
        },
        {
            "category": "Stadtgröße",
            "user_pref": _POP_LABELS[dom_pop],
            "pct": dist["population"][dom_pop],
        },
        {
            "category": "Meeresnähe",
            "user_pref": _OCEAN_LABELS[dom_ocean],
            "pct": dist["ocean"][dom_ocean],
        },
        {
            "category": "Klimazone",
            "user_pref": _LAT_LABELS[dom_lat],
            "pct": dist["latitude"][dom_lat],
        },
    ]

    avg_pct = sum(c["pct"] for c in comparisons) / len(comparisons)
    uniqueness = round(max(1, min(99, 100 - avg_pct)))

    return {
        "comparisons": comparisons,
        "uniqueness": uniqueness,
    }

# Fügt das Ergebnis der Bewertung einer Stadt dem user dict hinzu
def scoring(city, user, result):
    cost_index_categories = [20, 40, 60] # 4 Kategorien: < 20, < 40, < 60, >= 60
    population_categories = [100000, 500000, 1700000] #Kleinstadt, Mittelgroße Stadt, Großstadt, Megacity
    ocean_distance_categories = [5, 50] #Am Wasser, In der Nähe vom Wasser, Nicht am Wasser
    abs_latitude_categories = [30, 60] #Entfernung vom Äquator

    # Findet entsprechende Daten aus csv
    city_german = city[0]
    country_german = city[1]
    continent_german = city[2]
    city_ranking = city[3]
    city_cost = city[4]
    city_population = city[6]
    city_ocean_distance = city[7]
    city_lat = city[8]

    # Wenn geliked, dann den likes hinzufügen
    if result:
        user["liked_cities"].append(city_german)
        user["liked_countries"].append(country_german)
        user["liked_continents"].append(continent_german)
        user["liked_rankings"].append(city_ranking)
        user["count_liked_population"][categorise(population_categories, city_population)] += 1
        user["count_liked_ocean_distance"][categorise(ocean_distance_categories, city_ocean_distance)] += 1
        user["count_liked_abs_latitude"][categorise(abs_latitude_categories, city_lat)] += 1
        if city_cost != '':
            user["count_liked_cost"][categorise(cost_index_categories, city_cost)] += 1

    # ansonsten den dislikes
    else: 
        user["disliked_cities"].append(city_german)
        user["disliked_countries"].append(country_german)
        user["disliked_continents"].append(continent_german)
        user["disliked_rankings"].append(city_ranking)
        user["count_disliked_population"][categorise(population_categories, city_population)] += 1
        user["count_disliked_ocean_distance"][categorise(ocean_distance_categories, city_ocean_distance)] += 1
        user["count_disliked_abs_latitude"][categorise(abs_latitude_categories, city_lat)] += 1
        if city_cost != '':
            user["count_disliked_cost"][categorise(cost_index_categories, city_cost)] += 1


# Herausfinden an welcher Stelle in der Kategorie eine Stadt hingehört
def categorise(categories, value):
    i = 0
    for category in categories:
        if float(value) < float(category):
            return i
        i += 1
    return i

def standard_deviation(data):
    if not data:
        return 0
    # Erwartungswert
    exp = 0
    #Varianz
    var = 0
    for i in data:
        exp += i / len(data)
    for i in data:
        var += ((i - exp) ** 2) * (1 / len(data))
    return var ** 0.5

# Nächste Stadt aus csv holen – bevorzugt Städte aus wenig gesehenen Kategorien
# und gewichtet nach inverser globaler Zeig-Häufigkeit für ausgeglichene Abdeckung
def get_next_city(user, city_list, extra_seen=None, city_shown_counts=None):
    cost_categories       = [20, 40, 60]
    population_categories = [100000, 500000, 1700000]
    ocean_categories      = [5, 50]
    latitude_categories   = [30, 60]

    seen = set(user["liked_cities"] + user["disliked_cities"])
    if extra_seen:
        seen |= extra_seen

    # Abdeckung pro Bin berechnen (liked + disliked)
    cost_seen  = [user["count_liked_cost"][i]             + user["count_disliked_cost"][i]             for i in range(4)]
    pop_seen   = [user["count_liked_population"][i]       + user["count_disliked_population"][i]       for i in range(4)]
    ocean_seen = [user["count_liked_ocean_distance"][i]   + user["count_disliked_ocean_distance"][i]   for i in range(3)]
    lat_seen   = [user["count_liked_abs_latitude"][i]     + user["count_disliked_abs_latitude"][i]     for i in range(3)]

    # Alle noch nicht gesehenen Städte mit kombiniertem Score gewichten:
    # Score = bin_priority (wie unterrepräsentiert sind die Bins dieser Stadt?)
    #       × global_priority (wie selten wurde diese Stadt global angezeigt?)
    # Das verhindert, dass Städte in kleinen Bins (z.B. nur 4 Städte über 60° Breite)
    # überproportional häufig erscheinen, während Städte in großen Bins (80+ Megacities)
    # kaum gezeigt werden.
    available = []
    weights   = []
    for city in city_list:
        if city[0] in seen:
            continue

        cost_idx  = categorise(cost_categories,       city[4]) if city[4] != '' else None
        pop_idx   = categorise(population_categories, city[6])
        ocean_idx = categorise(ocean_categories,      city[7])
        lat_idx   = categorise(latitude_categories,   abs(float(city[8])))

        # Bin-Priorität: 1 / (coverage + 1) für jeden Bin, dann das Maximum nehmen.
        # Das stellt Diversität sicher, ohne einzelne Städte aus winzigen Bins zu bevorzugen.
        bin_scores = [
            1.0 / (pop_seen[pop_idx]     + 1),
            1.0 / (ocean_seen[ocean_idx] + 1),
            1.0 / (lat_seen[lat_idx]     + 1),
        ]
        if cost_idx is not None:
            bin_scores.append(1.0 / (cost_seen[cost_idx] + 1))
        bin_priority = max(bin_scores)

        # Globale Priorität: Städte die seltener gezeigt wurden bekommen höheres Gewicht
        global_count    = city_shown_counts.get(city[0], 0) if city_shown_counts else 0
        global_priority = 1.0 / (global_count + 1)

        available.append(city)
        weights.append(bin_priority * global_priority)

    if not available:
        return None
    return random.choices(available, weights=weights, k=1)[0]

# Gibt für jede Stelle jeder Kategorie Wert zurück, wie sehr sie user gefällt
def calculate_user_preference(user):
    user_preferences = {
        "cost": [0, 0, 0, 0],
        "sd_cost": 0,
        "weight_cost": 1,
        "population": [0, 0, 0, 0],
        "sd_population": 0,
        "weight_population": 1,
        "ocean_distance": [0, 0, 0],
        "sd_ocean_distance": 0,
        "weight_ocean_distance": 1,
        "latitude": [0, 0, 0],
        "sd_latitude": 0,
        "weight_latitude": 1,
    }

    for i in range(4):
        user_preferences["cost"][i] = user["count_liked_cost"][i] - user["count_disliked_cost"][i]
    for i in range(4):
        user_preferences["population"][i] = user["count_liked_population"][i] - user["count_disliked_population"][i]
    for i in range(3):
        user_preferences["ocean_distance"][i] = user["count_liked_ocean_distance"][i] - user["count_disliked_ocean_distance"][i]
    for i in range(3):
        user_preferences["latitude"][i] = user["count_liked_abs_latitude"][i] - user["count_disliked_abs_latitude"][i]

    # SD bleibt für die Anzeige der Präferenzbalken
    user_preferences["sd_cost"] = standard_deviation(user_preferences["cost"])
    user_preferences["sd_population"] = standard_deviation(user_preferences["population"])
    user_preferences["sd_ocean_distance"] = standard_deviation(user_preferences["ocean_distance"])
    user_preferences["sd_latitude"] = standard_deviation(user_preferences["latitude"])

    # Max-Abs als Scoring-Gewicht: funktioniert auch wenn der Nutzer konsequent eine Kategorie bevorzugt
    user_preferences["weight_cost"] = max(abs(x) for x in user_preferences["cost"]) or 1
    user_preferences["weight_population"] = max(abs(x) for x in user_preferences["population"]) or 1
    user_preferences["weight_ocean_distance"] = max(abs(x) for x in user_preferences["ocean_distance"]) or 1
    user_preferences["weight_latitude"] = max(abs(x) for x in user_preferences["latitude"]) or 1

    return user_preferences

# Beschreibungstext für Ergebnisse erzeugen
def get_city_description(city, used_descriptions):
    city_name = city[0]
    pop = float(city[6])
    dist = float(city[7])
    lat = abs(float(city[8]))
    cost_val = city[4]

    pop_str = f"{int(pop):,}"

    def pick_unique_and_format(templates, **kwargs):
        # 1. Wir prüfen nur die Templates (den reinen Text ohne eingesetzte Namen)
        available = [t for t in templates if t not in used_descriptions]
        
        # Falls alle Templates schon weg sind, nimm irgendeins aus der Liste
        choice_template = random.choice(available if available else templates)
        
        # 2. Wir markieren dieses Template als "benutzt"
        used_descriptions.add(choice_template)
        
        # 3. Jetzt füllen wir die Platzhalter ({name}, {pop}, etc.) mit den Werten
        return choice_template.format(**kwargs)

    if pop < 100000:
        pop_options = [
            "Mit etwa {pop} Einwohnern ist {name} eine gemütliche kleine Stadt, in der man schnell ankommt.",
            "{name} ist mit knapp {pop} Bewohnern eher beschaulich und bietet eine familiäre Atmosphäre.",
            "Wer Ruhe sucht, ist in {name} genau richtig – die Kleinstadt-Idylle mit {pop} Einwohnern entspannt sofort."
        ]
    elif pop < 500000:
        pop_options = [
            "Mit rund {pop} Einwohnern ist {name} eine lebendige Stadt, ohne dass es unübersichtlich wird.",
            "In {name} wohnen etwa {pop} Menschen – eine perfekte Größe für Entdecker, die kurze Wege lieben.",
            "Als mittelgroße Stadt mit {pop} Einwohnern bietet {name} genau die richtige Mischung aus Angebot und Gemütlichkeit."
        ]
    elif pop < 1700000:
        pop_options = [
            "Mit ungefähr {pop} Einwohnern ist {name} eine echte Großstadt mit vielfältiger Kultur.",
            "{name} ist mit {pop} Bewohnern ein bedeutendes urbanes Zentrum, das niemals langweilig wird.",
            "In dieser Metropole leben über {pop} Menschen, was für ein pulsierendes Stadtleben sorgt."
        ]
    else:
        pop_options = [
            "Mit mehr als {pop} Einwohnern ist {name} eine pulsierende Megacity, die niemals schläft.",
            "Das Leben in {name} ist bei über {pop} Einwohnern rasant, bunt und voller Energie.",
            "Als eine der ganz großen Weltstädte bietet {name} mit seinen {pop} Bewohnern eine schier endlose Vielfalt."
        ]
    
    pop_text = pick_unique_and_format(pop_options, name=city_name, pop=pop_str)

    cost_text = ""
    if cost_val != '':
        cost = float(cost_val)
        if cost < 20:
            cost_options = [
                "{name} ist besonders günstig, ideal um mit kleinem Budget viel zu erleben.",
                "Hier schont man den Geldbeutel, da die Lebenshaltungskosten erfreulich niedrig sind.",
                "Für preisbewusste Reisende ist diese Stadt ein wahres Paradies."
            ]
        elif cost < 40:
            cost_options = [
                "{name} bietet ein sehr ausgewogenes Preis-Leistungs-Verhältnis.",
                "Die Kosten vor Ort sind moderat und für die meisten Budgets gut machbar.",
                "Man bekommt hier viel geboten, ohne dass die Ausgaben sofort in die Höhe schießen."
            ]
        elif cost < 60:
            cost_options = [
                "{name} liegt im gehobenen Preisbereich, bietet dafür aber exzellente Qualität.",
                "Das Preisniveau ist etwas höher, was sich in der hohen Lebensqualität widerspiegelt.",
                "Wer bereit ist, etwas mehr auszugeben, wird von dem erstklassigen Angebot hier begeistert sein."
            ]
        else:
            cost_options = [
                "{name} zählt zu den exklusiveren Städten, in denen Qualität ihren Preis hat.",
                "Das Preisniveau ist hier sehr hoch, passend zum prestigeträchtigen Charakter der Stadt.",
                "In dieser exklusiven Lage sollte man ein etwas größeres Urlaubsbudget einplanen."
            ]
        cost_text = pick_unique_and_format(cost_options, name=city_name)

    if dist <= 5:
        ocean_options = [
            "Die Stadt liegt direkt am Wasser – frische Meeresluft ist hier garantiert.",
            "Man spürt förmlich die Nähe zum Ozean, der das Stadtbild maßgeblich prägt.",
            "Perfekt für Wasserratten: Der Strand oder Hafen ist nur einen Katzensprung entfernt."
        ]
    elif dist < 50:
        ocean_options = [
            "Das Meer ist nicht weit entfernt und lädt zu spontanen Ausflügen an die Küste ein.",
            "In kurzer Zeit erreicht man das Wasser, was für ein maritimes Flair sorgt.",
            "Die Nähe zur Küste bietet eine tolle Abwechslung zum Stadttrubel."
        ]
    else:
        ocean_options = [
            "Auch ohne Meeresnähe überzeugt die Stadt mit ihren ganz eigenen Freizeitmöglichkeiten.",
            "Das Landesinnere bietet hier einen ganz eigenen, charmanten Charakter.",
            "Hier steht nicht das Wasser, sondern die urbane Architektur und Kultur im Fokus."
        ]
    ocean_text = pick_unique_and_format(ocean_options)

    if lat < 30:
        lat_options = [
            "Die Nähe zum Äquator verspricht das ganze Jahr über tropische Wärme.",
            "Sonnenanbeter kommen hier voll auf ihre Kosten – das Klima ist herrlich warm.",
            "Warme Temperaturen und viel Sonnenschein prägen diesen Standort."
        ]
    elif lat < 60:
        lat_options = [
            "Hier genießt man klassische, angenehme Jahreszeiten.",
            "Das Klima ist ausgewogen – weder zu heiß noch zu kalt, ideal für Städtetrips.",
            "Man erlebt hier den vollen Charme der wechselnden Jahreszeiten."
        ]
    else:
        lat_options = [
            "Die nördliche bzw. südliche Lage sorgt für eine einzigartige, oft mystische Stimmung.",
            "Hier erlebt man ausgeprägte Jahreszeiten und die besondere Atmosphäre hoher Breiten.",
            "Das Klima ist hier oft erfrischend und verleiht der Stadt einen ganz eigenen Takt."
        ]
    lat_text = pick_unique_and_format(lat_options)

    return f"{pop_text} {cost_text} {ocean_text} {lat_text}"

def score_all_cities(user, user_preferences, city_list):
    """Berechnet Scores für alle Städte ohne 'bereits gesehen'-Filter. Für Gruppenvergleiche."""
    cost_cats  = [20, 40, 60]
    pop_cats   = [100000, 500000, 1700000]
    ocean_cats = [5, 50]
    lat_cats   = [30, 60]

    avg_weight = (user_preferences["weight_cost"] +
                  user_preferences["weight_population"] +
                  user_preferences["weight_ocean_distance"] +
                  user_preferences["weight_latitude"]) / 4
    country_bonus   = avg_weight * 0.5
    continent_bonus = avg_weight * 1.5

    scored = []
    for city in city_list:
        score = 0
        if city[1] in user["liked_countries"]:    score += country_bonus
        if city[1] in user["disliked_countries"]: score -= country_bonus
        if city[2] in user["liked_continents"]:   score += continent_bonus
        if city[2] in user["disliked_continents"]: score -= continent_bonus

        if city[4] != '':
            score += user_preferences["weight_cost"] * user_preferences["cost"][categorise(cost_cats, city[4])]
        score += user_preferences["weight_population"]     * user_preferences["population"][categorise(pop_cats, city[6])]
        score += user_preferences["weight_ocean_distance"] * user_preferences["ocean_distance"][categorise(ocean_cats, city[7])]
        score += user_preferences["weight_latitude"]       * user_preferences["latitude"][categorise(lat_cats, city[8])]
        scored.append((score, city))

    scored.sort(key=lambda x: (x[0], -float(x[1][3]) if x[1][3] != '' else 0), reverse=True)
    return scored


# Für jede Stadt kann basierend auf den Präferenzen des users ein Score berechnet werden --> gibt dann die drei Städte mit dem höchsten Score zurück
def calculate_city_score(user, user_preferences, city_list):
    cost_index_categories = [20, 40, 60] 
    population_categories = [100000, 500000, 1700000] 
    ocean_distance_categories = [5, 50] 
    abs_latitude_categories = [30, 60] 

    scored_cities = [] # Liste für Tupel aus (Score, City-Daten)

    for city in city_list:
        # 1. Überspringen, wenn der Nutzer die Stadt bereits bewertet hat
        if city[0] in user["liked_cities"] or city[0] in user["disliked_cities"]:
            continue

        score = 0

        # Durchschnittliches Kategorie-Gewicht als Basis für Land/Kontinent-Skalierung
        avg_weight = (user_preferences["weight_cost"] +
                      user_preferences["weight_population"] +
                      user_preferences["weight_ocean_distance"] +
                      user_preferences["weight_latitude"]) / 4
        country_bonus = avg_weight * 0.5
        continent_bonus = avg_weight * 1.5

        # Land-Präferenz (skaliert)
        if city[1] in user["liked_countries"]:
            score += country_bonus
        if city[1] in user["disliked_countries"]:
            score -= country_bonus
        if city[2] in user["liked_continents"]:
            score += continent_bonus
        if city[2] in user["disliked_continents"]:
            score -= continent_bonus

        # Kategorien-Scoring mit Max-Abs als Gewichtung
        if city[4] != '':
            score += user_preferences["weight_cost"] * user_preferences["cost"][categorise(cost_index_categories, city[4])]

        score += user_preferences["weight_population"] * user_preferences["population"][categorise(population_categories, city[6])]
        score += user_preferences["weight_ocean_distance"] * user_preferences["ocean_distance"][categorise(ocean_distance_categories, city[7])]
        score += user_preferences["weight_latitude"] * user_preferences["latitude"][categorise(abs_latitude_categories, city[8])]

        # Speichern als Tupel, um später nach dem Score sortieren zu können
        scored_cities.append((score, city))

    # 2. Sortieren nach Score; bei Gleichstand dient city_ranking als Tiebreaker (niedrigere Zahl = besser)
    scored_cities.sort(
        key=lambda x: (x[0], -float(x[1][3]) if x[1][3] != '' else 0),
        reverse=True
    )

    top_3_cities = []
    used_descriptions = set() # Speicher für bereits genutzte Satzbausteine
    
    for item in scored_cities[:3]:
        city_data = list(item[1]) 
        # Wir geben das Set an die Funktion weiter
        description = get_city_description(city_data, used_descriptions)
        city_data.append(description) 
        top_3_cities.append(city_data)

    # Analyse am Ende
    standard_deviations = {
        "cost" :user_preferences["sd_cost"], 
        "population": user_preferences["sd_population"], 
        "ocean_distance": user_preferences["sd_ocean_distance"], 
        "latitude": user_preferences["sd_latitude"]
    }
    sorted_sd = dict(sorted(
        standard_deviations.items(), 
        key=lambda item: item[1], 
        reverse=True))
    
    # Finde den maximalen SD-Wert für die Skalierung (Vermeidung von Division durch Null)
    max_sd = max(standard_deviations.values()) if any(standard_deviations.values()) else 1

    # Erstelle ein Dictionary mit lesbaren Namen und berechneten Prozentwerten
    display_sd = []
    
    # Mapping für deutsche Labels und Texte
    labels = {
        "cost": {"title": "Lebenskosten", "desc": "Wie viel Geld du in der Stadt ausgeben möchtest."},
        "population": {"title": "Stadtgröße", "desc": "Ob du dich in Megacities oder Kleinstädten wohl fühlst."},
        "ocean_distance": {"title": "Meeresnähe", "desc": "Die Distanz zum nächsten Strand oder Ozean."},
        "latitude": {"title": "Klimazone", "desc": "In welchem Klima du dich besonders wohl fühlst."}
    }

    for key, value in sorted_sd.items():
        percentage = (value / max_sd) * 100
        display_sd.append({
            "key": key,
            "title": labels[key]["title"],
            "desc": labels[key]["desc"],
            "value": round(percentage),
        })

    print(top_3_cities)
    return top_3_cities, display_sd, scored_cities


def _preference_vector(user):
    """Erstellt einen normalisierten 14-dimensionalen Geschmacksvektor (net_norm pro Bin-Gruppe)."""
    def net_norm(liked, disliked):
        net = [max(0, liked[i] - disliked[i]) for i in range(len(liked))]
        total = sum(net)
        return [x / total for x in net] if total > 0 else [1 / len(liked)] * len(liked)

    return (
        net_norm(user["count_liked_cost"],           user["count_disliked_cost"]) +
        net_norm(user["count_liked_population"],     user["count_disliked_population"]) +
        net_norm(user["count_liked_ocean_distance"], user["count_disliked_ocean_distance"]) +
        net_norm(user["count_liked_abs_latitude"],   user["count_disliked_abs_latitude"])
    )


def _cosine_similarity(a, b):
    dot   = sum(x * y for x, y in zip(a, b))
    mag_a = sum(x ** 2 for x in a) ** 0.5
    mag_b = sum(x ** 2 for x in b) ** 0.5
    return dot / (mag_a * mag_b) if mag_a and mag_b else 0


_SHARED_SENTENCES = {
    ("population", "Kleinstadt"):   "Ihr mögt beide kleine, gemütliche Städte",
    ("population", "Mittelgroß"):   "Ihr bevorzugt beide mittelgroße Städte",
    ("population", "Großstadt"):    "Ihr liebt beide das Großstadtleben",
    ("population", "Megacity"):     "Ihr liebt beide Megacities",
    ("ocean", "Direkt am Meer"):    "Ihr seid beide gerne direkt am Meer",
    ("ocean", "Küstennah"):         "Ihr mögt beide Städte in Küstennähe",
    ("ocean", "Im Landesinneren"):  "Euch zieht es beide ins Landesinnere",
    ("latitude", "Tropisch"):       "Ihr reist beide am liebsten in tropische Gefilde",
    ("latitude", "Gemäßigt"):       "Ihr bevorzugt beide gemäßigte Klimazonen",
    ("latitude", "Kühl & Polar"):   "Ihr liebt beide kühle, nordische Ziele",
    ("cost", "Günstig"):            "Ihr reist beide gerne budgetfreundlich",
    ("cost", "Erschwinglich"):      "Ihr achtet beide auf das Preis-Leistungs-Verhältnis",
    ("cost", "Gehoben"):            "Ihr reist beide gerne auf gehobenem Niveau",
    ("cost", "Luxuriös"):           "Ihr reist beide auf höchstem Niveau",
}


def find_travel_buddy(current_user, current_session_id, city_list, all_swipes):
    """Findet aus allen abgeschlossenen Sessions die ähnlichste.
    all_swipes: {session_id: [(iteration, city, country, continent, choice), ...]}
    """
    city_map  = {city[0]: city for city in city_list}
    my_vector = _preference_vector(current_user)

    # Meine Top-3 Städte vorausberechnen
    my_prefs  = calculate_user_preference(current_user)
    my_scored = score_all_cities(current_user, my_prefs, city_list)
    seen      = set(current_user["liked_cities"] + current_user["disliked_cities"])
    my_top3   = []
    for _, city in my_scored:
        if city[0] not in seen:
            my_top3.append(city)
            if len(my_top3) == 3:
                break

    best_sim        = -1
    best_personality = None
    best_buddy_user  = None
    total_others     = 0

    for session_id, swipes in all_swipes.items():
        if session_id == current_session_id:
            continue
        total_others += 1

        other_user = {
            "liked_rankings": [], "liked_cities": [], "liked_countries": [], "liked_continents": [],
            "count_liked_cost": [0,0,0,0], "count_liked_population": [0,0,0,0],
            "count_liked_ocean_distance": [0,0,0], "count_liked_abs_latitude": [0,0,0],
            "disliked_rankings": [], "disliked_cities": [], "disliked_countries": [], "disliked_continents": [],
            "count_disliked_cost": [0,0,0,0], "count_disliked_population": [0,0,0,0],
            "count_disliked_ocean_distance": [0,0,0], "count_disliked_abs_latitude": [0,0,0],
        }
        for _, city_name, _, _, choice in swipes:
            city_data = city_map.get(city_name)
            if city_data:
                scoring(city_data, other_user, choice == "like")

        sim = _cosine_similarity(my_vector, _preference_vector(other_user))
        if sim > best_sim:
            best_sim         = sim
            best_personality = get_personality_type(other_user)
            best_buddy_user  = other_user

    if total_others == 0 or best_personality is None or best_buddy_user is None:
        return None

    # Stadt aus meiner Top-3 finden, die beim Buddy am höchsten rankt
    buddy_prefs  = calculate_user_preference(best_buddy_user)
    buddy_scored = score_all_cities(best_buddy_user, buddy_prefs, city_list)

    buddy_seen = set(best_buddy_user["liked_cities"] + best_buddy_user["disliked_cities"])

    # Unseen-Ränge für Buddy und mich berechnen (konsistent mit der #1/#2/#3-Anzeige)
    buddy_unseen_rank = {}
    idx = 1
    for _, city in buddy_scored:
        if city[0] not in buddy_seen:
            buddy_unseen_rank[city[0]] = idx
            idx += 1

    my_unseen_rank = {}
    idx = 1
    for _, city in my_scored:
        if city[0] not in seen:
            my_unseen_rank[city[0]] = idx
            idx += 1

    # Shared city: aus meinen Top-3 die Stadt wählen, die beim Buddy am besten rankt
    # und die der Buddy noch nicht gesehen hat
    shared_city = None
    for c in sorted(my_top3, key=lambda c: buddy_unseen_rank.get(c[0], len(city_list))):
        if c[0] not in buddy_seen:
            shared_city = c
            break
    # Fallback falls alle my_top3-Städte vom Buddy schon gesehen wurden
    if shared_city is None and my_top3:
        shared_city = min(my_top3, key=lambda c: buddy_unseen_rank.get(c[0], len(city_list)))

    # Stärkste gemeinsame Präferenzdimension finden
    def dom(lst): return lst.index(max(lst)) if max(lst) > 0 else 0

    dims = [
        ("cost",       current_user["count_liked_cost"],            best_buddy_user["count_liked_cost"],            _COST_LABELS),
        ("population", current_user["count_liked_population"],      best_buddy_user["count_liked_population"],      _POP_LABELS),
        ("ocean",      current_user["count_liked_ocean_distance"],  best_buddy_user["count_liked_ocean_distance"],  _OCEAN_LABELS),
        ("latitude",   current_user["count_liked_abs_latitude"],    best_buddy_user["count_liked_abs_latitude"],    _LAT_LABELS),
    ]
    shared_stat  = None
    best_strength = -1
    for dim_name, my_counts, buddy_counts, labels in dims:
        my_d = dom(my_counts)
        bu_d = dom(buddy_counts)
        if my_d == bu_d:
            strength = my_counts[my_d] + buddy_counts[bu_d]
            if strength > best_strength:
                best_strength = strength
                shared_stat   = _SHARED_SENTENCES.get(
                    (dim_name, labels[my_d]),
                    f"Ihr teilt beide eine Vorliebe für {labels[my_d]}"
                )

    shared_city_my_rank    = my_unseen_rank.get(shared_city[0], 0) if shared_city else None
    shared_city_buddy_rank = buddy_unseen_rank.get(shared_city[0], 0) if shared_city else None

    buddy_top3 = []
    for _, city in buddy_scored:
        if city[0] not in buddy_seen:
            buddy_top3.append(city)
            if len(buddy_top3) == 3:
                break

    return {
        "match_pct":              round(best_sim * 100),
        "total_players":          total_others,
        "personality":            best_personality,
        "shared_city":            shared_city,
        "shared_city_my_rank":    shared_city_my_rank,
        "shared_city_buddy_rank": shared_city_buddy_rank,
        "shared_stat":            shared_stat,
        "buddy_top3":             buddy_top3,
        "avatar":                 random.choice(["😎", "🤠", "🤗", "🥳"]),
    }


def find_hidden_gem(user, scored_cities, city_avg_ranks, min_sessions=3):
    """Findet eine Stadt, die persönlich hoch rankt aber global selten gut abschneidet.
    scored_cities: vollständige Rangliste aus score_all_cities() (best-first).
    city_avg_ranks: dict aus get_city_avg_ranks().
    """
    if not city_avg_ranks or not scored_cities:
        return None

    seen  = set(user["liked_cities"] + user["disliked_cities"])
    total = len(scored_cities)

    # Top-3 des Users ermitteln (erste 3 nicht-gesehenen Städte)
    top3 = set()
    for _, city in scored_cities:
        if city[0] not in seen:
            top3.add(city[0])
            if len(top3) == 3:
                break

    best_score = -1
    best_city  = None
    best_info  = None

    for personal_rank, (_, city) in enumerate(scored_cities):
        name = city[0]

        if name in seen or name in top3:
            continue

        stats = city_avg_ranks.get(name)
        if not stats or stats["sessions"] < min_sessions:
            continue

        # Muss persönlich in den Top 25% liegen
        if personal_rank / total > 0.25:
            continue

        global_obscurity = stats["avg_rank"] / total   # [0,1]: 1 = immer am Ende
        personal_fit     = 1 - (personal_rank / total) # [0,1]: 1 = persönlich auf Platz 1

        gem_score = global_obscurity * personal_fit

        if gem_score > best_score:
            best_score = gem_score
            best_city  = city
            best_info  = {
                "personal_rank":   personal_rank + 1,
                "global_avg_rank": round(stats["avg_rank"] + 1),
                "total":           total,
                "sessions":        stats["sessions"],
            }

    if best_city is None:
        return None

    return {"city": list(best_city), "info": best_info}
