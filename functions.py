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


def get_personality_type(user):
    def norm(lst):
        total = sum(lst)
        if total == 0:
            return [1 / len(lst)] * len(lst)
        return [x / total for x in lst]

    lp = norm(user["count_liked_population"])       # [<100k, 100k-500k, 500k-1.7M, >1.7M]
    lc = norm(user["count_liked_cost"])             # [<20, 20-40, 40-60, >60]
    lo = norm(user["count_liked_ocean_distance"])   # [<5km, 5-50km, >50km]
    ll = norm(user["count_liked_abs_latitude"])     # [<30°, 30-60°, >60°]

    scores = {
        "metropolen_fan":   lp[3] * 3 + lp[2] * 1,
        "strandliebhaber":  lo[0] * 3 + ll[0] * 2,
        "kulturreisender":  ll[1] * 2 + lp[1] * 1 + lp[2] * 1 + lc[1] * 1 + lc[2] * 1,
        "backpacker":       lc[0] * 3 + lc[1] * 2,
        "luxusurlauber":    lc[3] * 3 + lp[3] * 1 + lp[2] * 1,
        "entdecker":        ll[2] * 2 + lp[0] * 2 + lo[2] * 1,
    }

    best_key = max(scores, key=scores.get)
    return PERSONALITY_TYPES[best_key]

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
def get_next_city(user, city_list, extra_seen=None):
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

    # Alle Bins mit ihrer Abdeckung sammeln und nach aufsteigender Abdeckung sortieren
    all_bins = (
        [("cost",       i, cost_seen[i])  for i in range(4)] +
        [("population", i, pop_seen[i])   for i in range(4)] +
        [("ocean",      i, ocean_seen[i]) for i in range(3)] +
        [("latitude",   i, lat_seen[i])   for i in range(3)]
    )
    all_bins.sort(key=lambda x: x[2])

    # Versuche eine Stadt aus dem am wenigsten abgedeckten Bin zu finden
    for category, bin_idx, _ in all_bins:
        candidates = []
        for city in city_list:
            if city[0] in seen:
                continue
            if category == "cost":
                if city[4] == '' or categorise(cost_categories, city[4]) != bin_idx:
                    continue
            elif category == "population":
                if categorise(population_categories, city[6]) != bin_idx:
                    continue
            elif category == "ocean":
                if categorise(ocean_categories, city[7]) != bin_idx:
                    continue
            elif category == "latitude":
                if categorise(latitude_categories, city[8]) != bin_idx:
                    continue
            candidates.append(city)

        if candidates:
            return random.choice(candidates)

    # Fallback: rein zufällig aus allen noch nicht gesehenen Städten
    available = [city for city in city_list if city[0] not in seen]
    return random.choice(available)

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
