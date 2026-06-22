import machine
import neopixel
import time
import random

# LED Stoppen: python3 -c "import serial,time; s=serial.Serial('/dev/cu.usbmodem1101',115200); s.dtr=True; s.rts=False; time.sleep(0.2); s.write(b'\r\x03\x03'); s.flush(); time.sleep(0.4); s.close()"

# ==============================================================================
# ZENTRALE KONFIGURATION
# ==============================================================================
GPIO_PIN = 4
ANZ_LEDS = 50

# --- GESCHWINDIGKEITSKURVE ---
START_PAUSE = 0.10   # Gesamte Einblendzeit für die erste LED (in Sekunden)
END_PAUSE = 0.0125     # Gesamte Einblendzeit für die letzte LED (in Sekunden)

# --- STOTTER-EINSTELLUNGEN ---
BASE_STOTTER_CHANCE = 0.60  # Chance bei LED 0 (0.60 = 60% Chance)
STOTTER_DAUER_Y = 0.40      # Maximale Zusatzsekunden für einen Stotterer am Start

# --- FADE-EINSTELLUNG ---
FADE_SCHRITTE = 20    # In wie vielen Zwischenstufen die LED heller wird (höher = weicher)

# --- FARBE & HELLIGKEITS-VERLAUF ---
# Feste Orange-Gelb-Farbe; jede LED bekommt eine andere Endhelligkeit.
# LED 0 = am hellsten (Stadt passt sehr gut), letzte LED = am dunkelsten (passt kaum).
ZIEL_FARBE = (255, 150, 0)   # Orange-Gelb
HELL_MAX = 1.0               # Endhelligkeit der ersten LED (hellste Stadt)
HELL_MIN = 0.08              # Endhelligkeit der letzten LED (dunkelste Stadt)
# ==============================================================================

# Initialisierung
pin = machine.Pin(GPIO_PIN, machine.Pin.OUT)
np = neopixel.NeoPixel(pin, ANZ_LEDS)

def alle_aus():
    for i in range(ANZ_LEDS):
        np[i] = (0, 0, 0)
    np.write()

print("=" * 60)
print(" SUKZESSIVES EINBLENDEN (FADE-IN) + STOTTERN")
print("=" * 60)
print("Das Programm läuft von alleine. STRG+C zum Beenden.")
print("-" * 60)

while True:
    try:
        # Feste Orange-Gelb-Zielfarbe für alle LEDs
        ziel_r, ziel_g, ziel_b = ZIEL_FARBE

        alle_aus()

        # Die 50 LEDs nacheinander aktivieren
        for led in range(ANZ_LEDS):

            # --- 1. GESAMTZEIT FÜR DIESE LED BERECHNEN ---
            fortschritt = led / (ANZ_LEDS - 1)
            base_pause = START_PAUSE - (fortschritt * (START_PAUSE - END_PAUSE))

            # Stotter-Logik
            abnehmender_faktor = (ANZ_LEDS - 1 - led) / (ANZ_LEDS - 1)
            if random.random() < (BASE_STOTTER_CHANCE * abnehmender_faktor):
                zusatz_zeit = STOTTER_DAUER_Y * abnehmender_faktor * random.uniform(0.5, 1.5)
                base_pause += zusatz_zeit

            # Endhelligkeit dieser LED: Verlauf von hell (LED 0) zu dunkel (letzte LED)
            ziel_helligkeit = HELL_MAX - (fortschritt * (HELL_MAX - HELL_MIN))

            # --- 2. DIE LED SANFT AUFBLENDEN ---
            # Wir teilen die berechnete Wartezeit durch die Anzahl der Fade-Schritte
            mikro_pause = base_pause / FADE_SCHRITTE

            for schritt in range(1, FADE_SCHRITTE + 1):
                # Helligkeitsfaktor des Fade-Ins (geht von 1/FADE_SCHRITTE bis 1.0),
                # multipliziert mit der Endhelligkeit dieser LED im Verlauf
                helligkeits_faktor = (schritt / FADE_SCHRITTE) * ziel_helligkeit

                # Farbe für den aktuellen Zwischenschritt herunterskalieren
                aktuelles_r = int(ziel_r * helligkeits_faktor)
                aktuelles_g = int(ziel_g * helligkeits_faktor)
                aktuelles_b = int(ziel_b * helligkeits_faktor)

                # LED aktualisieren
                np[led] = (aktuelles_r, aktuelles_g, aktuelles_b)
                np.write()

                # Einen winzigen Moment warten, bevor die LED noch ein Stück heller wird
                time.sleep(mikro_pause)

        # Am Ende die voll beleuchtete Kette kurz stehen lassen
        time.sleep(2.0)
        alle_aus()
        time.sleep(0.5)

    except KeyboardInterrupt:
        print("\nProgramm beendet.")
        alle_aus()
        break
