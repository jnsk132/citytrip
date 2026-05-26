# Reiseziel Finder – Interaktive Webanwendung zur personalisierten Reiseempfehlung

#### Author1: Jonas Knorr / 2199661  
#### Video:  https://youtu.be/5hcnIm1pBlE

## Projektübersicht

Der **Reiseziel Finder** ist eine interaktive Webanwendung, die Nutzern dabei hilft, ein passendes Reiseziel zu finden. Die Anwendung funktioniert nach dem Prinzip eines Swipe- oder Like/Dislike-Systems, ähnlich wie bekannte Matching-Plattformen. Der Nutzer bewertet nacheinander verschiedene Städte anhand von Bildern und trifft eine Entscheidung per „Like“ oder „Dislike“. Basierend auf diesen Entscheidungen kann eine individuelle Reiseempfehlung ermittelt werden.

Ziel des Projekts war es, eine benutzerfreundliche, visuell ansprechende und funktionale Webanwendung zu entwickeln, die schnell und einfach nutzbar ist. 

---

## Projektstruktur und Dateien

### app.py

Die Datei `app.py` erhält alle Anweisungen für Flask. Hier findet die grundlegende Logik des Programmes statt.

Im static Ordner existiert eine csv Datei, die alle Länder, gemeinsam mit Daten zu Einwohneranzahl oder Koordinaten enthält. Diese Datei wird geladen und stellt die Datengrundlage des Programmes dar. Um die gesendeten Entscheidungen des Nutzers zu speichern, wird eine Flask session erstellt. Die session speichert grundlegende Daten z.B. wie oft ein Nutzer schon Bewertungen abgegeben hat. Zentral ist aber das user-dict, das alle Likes und Dislikes speichert. 

Immer wenn die Quiz Seite aufgerufen wird, wird eine zufällige neue Stadt gewählt, die noch nicht bewertet wurde. Hat man die 20. Stadt bewertet, wird man automatisch auf die results Seite weitergeleitet. Hier werden die 3 Städte angezeigt, die am besten mit den Präferenzen des Nutzers übereinstimmen.

Flask wurde aufgrund seiner Flexibilität für diese Anwendung gewählt. Für ein Projekt dieser Größe ist Flask ideal, da es eine schnelle Entwicklung ermöglicht. Die die Daten werden in Sessions gespeichert, da keine dauerhafte Datenbank erforderlich ist. Das Projekt ist bewusst als leichtgewichtige Webanwendung konzipiert.


---

### functions.py

Die Datei `functions.py` enthält ausgelagerte Funktionen, die in app.py genutzt werden. Essentiell ist die scoring() Funktion. Wird eine Stadt geliked, wird nicht nur die Stadt gespeichert, sondern auch z.B. ihr Preisindex oder welche Entfernung sie vom Ozean hat.

Die calculate_user_preferences() Funktion berechnet dann, welche Art von Stadt der Nutzer mag. Sie gibt ein Dictionary zurück, was später für die Berechnungen der passendsten Städte essentiell ist. Mehr dazu später. 

get_city_description() erstellt für die Anzeige auf der results Seite, einen passenden kurzen Beschreibungstext. Für jede Kategorie wurden je drei Texte hinterlegt. So kommt kein Text doppelt vor, auch wenn alle drei Städte die selben Eigenschaften haben. 

Die Funktion calculate_city_score() berechnet für jede noch nicht bewertete Stadt einen numerischen Score, der angibt, wie gut sie zu den bisherigen Nutzerpräferenzen passt. Dafür werden die Merkmale der Stadt mit denen der geliketen Städte verglichen. Je ähnlicher eine Stadt den positiv bewerteten Städten ist, desto stärker erhöht sich ihr Score. Gleichzeitig wird geprüft, wie stark sie den disliked Städten ähnelt: hohe Ähnlichkeit führt hier zu einer Reduzierung des Scores. Die einzelnen Merkmale können unterschiedlich gewichtet werden, um bestimmte Faktoren stärker zu berücksichtigen. Am Ende werden alle Städte nach ihrem Score sortiert und die drei mit dem höchsten Wert als Empfehlung ausgegeben.

---

### HTML Dateien

Die HTML-Dateien definieren die Benutzeroberfläche der Anwendung. Sie kombinieren HTML, CSS und Jinja-Templating.

Das Design wurde bewusst minimalistisch gehalten. Der dunkle Hintergrund mit kontrastreichen Akzentfarben sorgt für eine klare  Hierarchie. Die Stadtbilder sind mit einem Hover-Zoom-Effekt versehen und durch einen Farbverlauf überlagert, damit der Text gut lesbar bleibt. Die Buttons sind visuell klar unterscheidbar (grün für Like, rot für Dislike), um eine intuitive Bedienung zu ermöglichen.