import csv
import sys

CSV_PFAD = "static/worldcities_ranked_german.csv"


def staedte_deutsch():
    """Liest die deutschen Stadtnamen (erste Spalte) aus der CSV."""
    with open(CSV_PFAD, encoding="utf-8") as f:
        return [zeile["city_german"] for zeile in csv.DictReader(f)]


if __name__ == "__main__":
    # Indizes von der Kommandozeile, sonst Standardwerte
    start = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    ende = int(sys.argv[2]) if len(sys.argv) > 2 else 10

    staedte = staedte_deutsch()
    for i, stadt in enumerate(staedte[start:ende], start=start):
        print(f"{stadt}")
