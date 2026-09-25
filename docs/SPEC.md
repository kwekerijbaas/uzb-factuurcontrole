# Rekenspecificatie UZB-urencontrole

> **Doel van dit document:** de volledige, herbouwbare specificatie van de
> UZB-uren- en factuurcontrole vastleggen. Gevalideerd tegen week 25/2026
> (Level One + Sterk Werk).
>
> **Geïmplementeerd:** de bron-ingest (§2) in `backend/app/services/ingest/`,
> de CAO-toeslagberekening (§4) in `backend/app/services/calc/` (geseed vanuit
> `backend/app/services/seed/`), en de tariefmapping + bedragberekening (§5)
> in `backend/app/services/tarief/`.
> De tariefkaart-upload (§6) zit in `services/ingest/` en
> `services/tarief/`; de factuurcontrole (§7) in
> `services/ingest/factuur.py` en `services/factuurcontrole.py`.

## 1. Doel

Per week, per uitzendbureau (UZB):
1. **Urenoverzicht** genereren: gewerkte uren per medewerker, gesplitst in
   toeslagcategorieën, met het bijbehorende inkoopbedrag.
2. **Factuurcontrole**: het overzicht vergelijken met de ontvangen UZB-factuur
   en afwijkingen classificeren.

## 2. Invoer

| Bron | Formaat | Inhoud |
|---|---|---|
| **SNOOP-export** | `.xlsx` | Per medewerker de **tarief-code** (loonschaal) en de kolom **Werkgever op datum shift**, waaruit het uitzendbureau wordt afgeleid. |
| **Nitea-registratie** | `.pdf` (+ `.xlsx`) | Werkelijk gewerkte uren per medewerker per dag. **Nitea's "werk tijd" is leidend.** Nachtdiensturen zijn in de Nitea-Excel **rood** gemarkeerd. Het is een **urenoverzicht, geen bureau-overzicht**: het kan mensen van een ander bureau bevatten (zie §4). |
| **Tariefkaart** | `.xlsx` | Per UZB een tabblad met per loonschaal de tarieven per toeslagcategorie. Heeft een **ingangsdatum** (zie §6). |
| **UZB-factuur** | `.pdf` | Voor de controlestap. Per UZB een eigen indeling (zie §5). |

## 3. Uitvoer

Per UZB een `.xlsx` met de tabbladen **Totaal week** (opent als eerste),
**Per dag**, **Tarieven** en **Afwijkingen**.

Het resultaat wordt ook **bewaard** (`match_periode` + `berekende_uren`,
inclusief loonschaal en bedrag). De factuur komt dagen tot weken later binnen;
door de week te bewaren hoeven SNOOP en Nitea daarvoor niet opnieuw ingelezen te
worden en wordt tegen exact dezelfde berekening vergeleken. Weken blijven twee
jaar staan (`bewaartermijn_jaren`); oudere worden opgeruimd zodra er een nieuwe
week wordt verwerkt.

De factuurcontrole levert een **apart matchingsbestand** met de tabbladen
**Samenvatting**, **Bevindingen**, **Koppelingen** en **Bevindingenmail**.

## 4. Toeslagberekening (CAO Glastuinbouw 2025–2026)

Per medewerker per week worden de gewerkte uren in **buckets** gesplitst.

### Buckets
| Bucket | Omschrijving |
|---|---|
| **0%** | Normale uren |
| **35%** | Overwerk: uren boven de weeknorm van **38 u/week** |
| **50%** | Nacht (ma–za 00:00–06:00), avond (ma–vr 20:00–24:00), zaterdagmiddag (za 15:00–24:00); tevens >10 u/dag en >48 u/week |
| **100%** | Zondaguren |
| **feestdag** | Werken op een CAO-feestdag |

### Feestdagen
De doorbetaalde feestdagen (art. 16 lid 2) worden **berekend**, niet per jaar
opgeschreven: Pasen via de gregoriaanse rekenregel, Hemelvaart en Pinksteren
daaruit afgeleid, Koningsdag op 27 april (26 april als die op zondag valt) en
Bevrijdingsdag alleen in een lustrumjaar. Een vaste lijst per CAO-periode liep
af, waarna elke verwerkte week stilzwijgend nul feestdagen had en de
feestdagtoeslag niet werd berekend.

### Regels
- **Een regel zonder werktijd telt niet mee.** Staat Nitea's "werk tijd" op
  0:00, dan is er die dag niets gewerkt; de regel wordt overgeslagen met een
  afwijking. Eerder viel zo'n regel terug op "alleen pauze aftrekken", en bij
  begin == eind werd dat een dienst van vierentwintig uur.
- **Toeslagen stapelen niet** — de hoogste van toepassing zijnde toeslag geldt
  (CAO art. 28 lid 2b).
- Reken op **minuut-resolutie**.
- **Diensten over middernacht** worden gesplitst op de dag-/datumgrens.
- Rond elke bucket af op **kwartieren**, met behoud van het **Nitea-weektotaal**
  (de som van de buckets moet gelijk blijven aan de door Nitea geregistreerde uren).

**De SNOOP-planning wordt niet met de registratie vergeleken.** Nitea legt vast
wie er werkelijk gewerkt heeft en wordt vóór het verwerken al gecontroleerd; een
verschil met de planning zegt dus niets over de te factureren uren. SNOOP dient
alleen als bron voor de loonschaal. Wie de planning tóch wil bewaken, zet
`WeekParameters.vergelijk_planning` aan.

### Zonder loonschaal geen bedrag — maar de week gaat door
Iedereen die in een week gewerkt heeft, hoort een loonschaal te hebben: zonder
schaal is er geen tarief en dus geen bedrag. Een ontbrekende schaal (of een
schaal die niet op de tariefkaart staat) **blokkeert de week niet**: de rest
van de ploeg mag niet wachten op één naam. De week wordt verwerkt en bewaard,
en die persoon staat met uren maar zonder bedrag in het overzicht:

- op het **resultaatscherm** na het verwerken een tabel "Zonder tarief" met
  naam, uren, reden en een link die onder Uitzendkrachten direct op die
  persoon landt (`/uzk?zoek=naam`); het overzicht wordt vanaf datzelfde scherm
  gedownload;
- bovenaan het tabblad *Totaal week* een **LET OP** met de namen en de reden
  (geen loonschaal / schaal niet op de kaart), en in de kolom Bedrag de tekst
  "geen tarief" in plaats van € 0,00;
- dezelfde waarschuwing als eerste melding op het tabblad *Afwijkingen*;
- bij de factuurcontrole een eigen bevinding **"Bedrag niet te controleren
  (geen tarief bij ons)"** met als actie: schaal invullen, week opnieuw
  verwerken, factuur opnieuw controleren, en het bedrag tot die tijd niet
  goedkeuren. Er gaat dus geen creditverzoek naar het bureau op basis van een
  bedrag dat bij ons simpelweg ontbrak.

De schaal komt in volgorde uit (1) de SNOOP-regel van de week zelf en (2) de
laatst bekende schaal op de uitzendkracht. Die tweede is met de hand in te
vullen onder **Uitzendkrachten**, en wordt getoetst aan de tariefkaart die
vandaag geldt — een typefout zou anders alsnog een bedrag van nul opleveren.
Namen worden vóór de controle vastgelegd, zodat wie een schaal mist meteen op
die lijst staat.

**Namen worden op gelijkenis gekoppeld.** Dezelfde persoon staat in Nitea,
SNOOP en de uitzendkrachtenlijst niet altijd hetzelfde geschreven: `Cristian`
tegenover `Christian`, `Visile` tegenover `Vasile`, `Robert Ionut Grasu`
tegenover `Ionut Robert Grasu`, `Elena Grasu` tegenover `Raluca Elena Grasu`.
Vindt de app geen regel op de exacte naam, dan koppelt hij op **achternaam**,
met de voornaam of initiaal als scheidsrechter bij naamgenoten (bij Sterk Werk
werken drie mensen Grasu). Delen twee kandidaten de hoogste score, dan wordt er
niet gekoppeld — een gok levert stilzwijgend een verkeerd tarief op. Elke
koppeling op gelijkenis komt als melding in het resultaat, met beide
schrijfwijzen, zodat een verkeerde koppeling zichtbaar is. Dezelfde regels
gelden voor het koppelen van factuurregels (§7); de logica staat in
`services/namen.py`.

**Handmatig ingevulde schalen zijn beschermd.** Een met de hand ingevulde
schaal (gemarkeerd met ✎, met de naam van wie hem invulde) wint bij het
verwerken van de SNOOP-waarde en wordt door bestanden niet stilzwijgend
overschreven. Wijkt een upload ervan af, dan volgt **per uitzendkracht een
ja/nee-vraag** op het resultaatscherm, met de naam van de invuller erbij:
"Ja, bestandswaarde overnemen" of "Nee, handmatig houden". Bij ja vervalt de
bescherming (en de naam) en overschrijven volgende imports geruisloos; bij nee
blijft de handmatige waarde staan en wordt het bij een volgende afwijkende
upload opnieuw gevraagd.

**Loskoppelen als de vergrendelde waarde achterhaald raakt.** Een jeugdkracht
die van leeftijd verandert krijgt in SNOOP vanzelf een nieuwe schaal; staat de
oude nog handmatig vast, dan blijft de app die gebruiken totdat iemand het
merkt (typisch bij een afwijking op de factuurcontrole). Bij elke uitzendkracht
met een ✎-markering staat daarom de knop **"volgt weer SNOOP"**: die heft de
vergrendeling op zónder de waarde te wijzigen. De schaal blijft zichtbaar staan
tot de eerstvolgende verwerkte week; die neemt dan automatisch over wat SNOOP
op dat moment meelevert, precies zoals bij een nieuwe uitzendkracht.

Zonder deze knop was de enige weg om een vergrendeling op te heffen de
ja/nee-vraag bij een upload — die verschijnt alleen als de nieuwe waarde al
bekend is uit dat bestand. Voor een groeiende jeugdkracht is er geen upload die
dat triggert; de schaal moest dan telkens met de hand worden bijgewerkt, wat in
de praktijk resulteerde in een schaal die maanden achterliep totdat het
opviel (september 2026: Level One jeugd).

**Het weeknummer komt uit de bestanden.** Nitea is leidend; het overzicht moet
precies één ISO-week beslaan en de SNOOP-export moet dezelfde week dekken,
anders wordt de upload geweigerd. Een getypt weeknummer ging te vaak fout en
zette de week onder het verkeerde nummer vast. Een tóch verkeerd bewaarde week
is te verwijderen op de Factuurcontrole-pagina; opnieuw verwerken zet hem goed
terug.

### Een schaal die geen tarief oplevert
De loonschaal staat in SNOOP, maar de app kan er geen tarief bij vinden. Drie
oorzaken, elk met een eigen melding:

1. **De schaal hoort bij een ander bureau** — "D2 SW" bij iemand die onder
   Level One staat. De melding noemt dat bureau met naam en vraagt de persoon
   te verplaatsen; voorheen was dit een bedrag van nul zonder zichtbare
   oorzaak. (In september 2026 stonden negenentwintig Sterk Werk-krachten zo
   onder Level One.)
2. **De schaal staat niet op de kaart** — bijvoorbeeld een kale "B2" bij Level
   One, waar B2 Flex, B2 Vast en B2 Seizoen elk een ander tarief hebben.
3. **Eén tariefkolom ontbreekt** — de kaart heeft de schaal wél, maar niet de
   categorie waarin een deel van de uren valt (de jeugdkaart heeft geen
   feestdagtarief). Die uren vielen stilzwijgend uit het bedrag: de persoon
   leek gewoon afgerekend terwijl zijn bedrag te laag was. Dit staat nu als
   **"Deels zonder tarief"** op het resultaatscherm, met de uren erbij, en het
   bedrag is in het overzicht gemarkeerd.

De schaalvertaling is verder onafhankelijk van schrijfwijze: het
seizoensachtervoegsel mag "Seizoen", "Seizoens" of "Seizoenskrachten" zijn, de
kaartcode komt altijd in hoofdletters, en een jeugdschaal met trede
("C2 18 jaar jeugd") houdt die trede (18C2) in plaats van op trede 2 uit te
komen.

### Mensen van een ander bureau in het Nitea-overzicht
Het Nitea-overzicht bevat soms mensen van een ander bureau. Wie **niet in de
SNOOP-export van deze week** staat maar wel op de uitzendkrachtenlijst van een
ander bureau (met loonschaal), wordt bij het verwerken **overgeslagen met een
melding** ("staat op de uitzendkrachtenlijst van Sterk Werk … niet meegeteld").
Staat hij wél in de SNOOP van dit bureau, dan werkt hij deze week hier en telt
hij gewoon mee. Level One en zijn jeugd-payroll delen hun bestanden en gelden
niet als "ander bureau" voor elkaar.

Is iemand tóch onder het verkeerde bureau beland (dat gebeurde vóór deze
regel, in week 25), dan past zijn schaal daar op geen kaart: "D4 SW" bij
iemand onder Level One. De melding zegt dan dat het een Sterk Werk-schaal is
en biedt één knop **"Verplaats naar Sterk Werk en sla 'D4 SW' op"**. Bij elke
uitzendkracht staat daarnaast een keuze **Verplaats** naar een ander bureau.
De weekresultaten gaan mee met de persoon; bestaat de naam onder het
doelbureau al, dan worden de rijen samengevoegd (schaal en code van het doel
blijven staan en worden alleen aangevuld).

### Lege begin- en eindtijd in het Nitea-overzicht
Het overzicht 'Medewerker uren' heeft de kolommen Nr, Medewerker, Datum,
**Begin tijd**, **Einde tijd**, **Werk tijd** en **Pauze tijd**. Bij nacht- en
middagdiensten laat Nitea de begin- en eindtijd regelmatig leeg, terwijl de
werktijd er wel staat. De tijden worden daarom op **kolompositie** gelezen en
niet op volgorde: aan de volgorde alleen is niet te zien of een losse tijd de
begin-, eind-, werk- of pauzetijd is.

- Staat alleen de **begintijd**, dan volgt het einde uit begin + werktijd +
  pauze.
- Staat alleen de **eindtijd**, dan volgt het begin uit einde − werktijd −
  pauze. Eerder werd die eindtijd als begintijd gelezen en liep een
  nachtdienst als dagdienst mee.
- Staan **beide niet**, dan valt de app terug op de **SNOOP-planning** van
  diezelfde medewerker en dag: Nitea blijft leidend voor het aantal uren, maar
  de klok waarop de toeslag wordt bepaald komt dan uit de geplande dienst. Dat
  kan alleen als die dag precies **één** geplande dienst heeft die lang genoeg
  is om de gewerkte tijd in kwijt te kunnen; bij twee of meer geplande
  diensten, of een planning die korter is dan de gewerkte tijd, is de aanname
  te onzeker en telt de tijd alsnog tegen 0% mee. In alle gevallen komt er een
  afwijking bij, zodat te zien is waar de toeslag op gebaseerd is en waar hij
  ontbreekt. Eerder verdween zo'n regel volledig — in week 32/2026 ging het om
  24,25 uur bij één persoon — en gaf elke nacht- of middagdienst zonder klok
  een handmatig te controleren afwijking op de factuur.

### Verificatie van nachtdiensten die Nitea wél met tijden geeft
De SNOOP-planning dient ook als tweede, onafhankelijke bron wanneer Nitea de
begin- en eindtijd wél geeft, specifiek voor diensten die nacht- of
avondtoeslag raken (00:00–06:00 of 20:00–24:00). Dit vangt het patroon
waardoor de vorige fouten ontstonden: niet een lege tijd, maar een **verkeerd
gelezen of verkeerd geklokte** tijd — een eindtijd die als begintijd wordt
gelezen, of een dienst die op de verkeerde datum terechtkomt. Zo'n fout is aan
de Nitea-bracket zelf vaak niet te zien: hij verschuift de dienst juist weg uit
het toeslagvenster, waardoor hij een gewone dagdienst lijkt.

Daarom telt zowel de Nitea-tijd als de **geplande** tijd mee bij het bepalen of
een dienst gecontroleerd moet worden: raakt een van beide een toeslagvenster,
en wijkt de Nitea-tijd meer dan **90 minuten** (`tolerantie_nachtdienst_minuten`)
af van de planning, dan volgt een afwijking (`nachtdienst_afwijkend`). Net als
bij de terugval hierboven geldt dit alleen bij precies **één** geplande dienst
die dag; bij twee of meer, of geen planning, is er geen eenduidige tweede bron
om tegen af te zetten en blijft het stil.

Deze controle staat **altijd aan**, los van `vergelijk_planning` (dat de
algemene, optionele vergelijking van uren en tijden regelt — SPEC hierboven,
"De SNOOP-planning wordt niet met de registratie vergeleken"). De marge van 90
minuten is bewust ruimer dan de 15 minuten die daar geldt: een nachtdienst
begint of eindigt vaker een half uur eerder of later zonder dat er iets mis
is, en dat is geen reden om te melden. Deze check is bedoeld om een
waarschijnlijke fout te signaleren, niet elke normale afwijking.

### Nachtdiensten in het Nitea-overzicht
Een dienst over middernacht kan in de PDF met een **einddatum** vóór de
eindtijd staan (`03-08-2026 22:57 04-08-2026 8:00 8:00 1:00`); die wordt
gelezen en op middernacht gesplitst. Staat er **geen eindtijd** (drie tijden,
waarvan begin–einde de werktijd bij lange na niet verklaart), dan wordt het
einde afgeleid uit begin + werktijd + pauze, in plaats van een dienst van
zeventien uur met één gewerkt uur aan te nemen. Regels die op een
registratieregel lijken maar niet te lezen zijn, en regels die anders gelezen
zijn dan ze er staan, komen als melding in het resultaat ("Nitea: N regels
niet of anders gelezen") — een stil weggelaten dag is een te laag weektotaal
dat niemand opmerkt (week 32/2026, Sylwia Piatek: 1,75 uur).

### De jaarlijst bevat meer dan de uitzendbureaus
Een SNOOP-lijst over een heel jaar bevat naast de ingerichte bureaus ook
**eigen medewerkers** en **bureaus zonder tariefkaart** (uitzendplatform
Temper, met schaal "Temper 2026"). Die namen worden **overgeslagen en
gerapporteerd** in plaats van het hele bestand te weigeren: één zo'n naam mag
de overige driehonderd niet tegenhouden. Alleen als er geen énkele regel te
plaatsen is, wordt het bestand geweigerd — dan is het een verkeerd bestand.

SNOOP schrijft Cervokordaat ook kortweg als **"Kordaat"**; de schalen staan
daar zonder achtervoegsel ("B2", "C4") en passen op de CK-kaart.

Het resultaatscherm van een upload toont daarom drie dingen naast de
samenvatting per bureau:

1. **Wel ingeladen, maar zonder tarief** — de schaal staat in het bestand maar
   levert op de kaart van dat bureau geen tarief op, meestal omdat het
   achtervoegsel ontbreekt ("B2" in plaats van "B2 Flex", "B2 Vast" of
   "B2 Seizoen", die elk een ander tarief hebben). Per regel een invulveld om
   het meteen te corrigeren.
2. **Niet ingeladen** — naam, werkgever, schaal en de reden.
3. De ja/nee-vragen bij handmatig ingevulde schalen (zie hierboven).

### Apart gefactureerd
Sommige uitzendkrachten factureert het bureau los (techniek, apart geboekt).
Bij **Uitzendkrachten** staat per persoon **"Apart factureren"** (met de naam
van wie het instelde). Zo iemand krijgt:

- bij het verwerken van een week een **eigen weekoverzicht** (uren, tarieven,
  afwijkingen) naast het hoofdoverzicht, dat hem niet meetelt — het
  hoofdoverzicht past dan naast de hoofdfactuur;
- bij de factuurcontrole een **eigen controle** met eigen matchingsbestand en
  bevindingenmail (§7); het hoofdblok telt hem niet mee en meldt hem ook niet
  als "niet gefactureerd".

De markering geldt ook voor al bewaarde weken: de controle kijkt naar de
instelling van nú.

## 5. Tariefmapping per UZB

Elke UZB heeft een eigen tabblad in de tariefkaart, een eigen code-mapping en
eigen facturatie-conventies. Het inkoopbedrag per medewerker = som over de
buckets × het bijbehorende tarief.

> **Implementatie:** de conventies staan als data in
> `backend/app/services/tarief/uzb.py`; de bedragberekening in `bedrag.py`.
> De koppeling loopt via de **toeslag-bron** uit de calc-trace (`nacht`,
> `avond`, `feestdag`, `overwerk_35`, …) en niet via het percentage, omdat één
> percentage meerdere tarieven kan hebben (nacht/avond/zaterdag/feestdag zijn
> alle 50%, maar Sterk Werk kent een apart nachtuur-tarief en Level One een
> apart feestdag-tarief). Een bron die een UZB niet doorbelast (bv. de
> dag-grens bij Sterk Werk) valt terug op het basistarief; de uren verdwijnen
> dus niet.

### 5.1 Level One — regulier (`L1`)
- **Code-mapping:** `"B2 Flex"` → `B2F`, `"B4 Vast"` → `B4V`,
  `"C2 Seizoens"` → `C2S`, `"… Payroll"` → V-tarief.
- **Tariefkolommen:** `100/135` (samen) | `150` | `200` | `feestdag`.
- **Bedrag** = `(u0 + u35)·t100 + u50·t150 + u100·t200 + ufeest·tfeest`.
  (135%-overwerk wordt tegen het **basistarief** gefactureerd — dus samen met 0%.)
- **Conventies:** dag-grens (>10 u/dag = 50%) **wel** toepassen; feestdag op
  apart feestdag-tarief; pauze op de **laagste** toeslag.

### 5.2 Level One — Volwassenen / Payroll
- Payroll-schalen (bv. `C6 Payroll`) lopen via de Level One-mapping mee
  (`… Payroll` → V-tarief); er is geen apart uitzendbureau voor.
- **Vervallen per augustus 2026:** dit betrof één medewerker, waarvan afscheid
  is genomen. De mapping blijft staan voor eerdere weken.
- De factuur bevat naast loon soms een **reiskostenvergoeding** (aparte regel,
  aantal × tarief) die niet in de urenberekening zit maar wél op de factuur —
  die telt in het bedrag mee, niet in de uren.

### 5.3 Level One — Jeugd (`L1_JEUGD`)
- Jeugdschalen per leeftijd (bv. `B 15 jaar Jeugd`, `C2 18 jaar jeugd`).
- **Code-mapping:** `"B 17 jaar Jeugd"` → `17B2` (leeftijd + letter + trede);
  payroll- en flexschalen op hetzelfde tabblad volgen de L1-regels
  (`"B2 Flex"` → `B2F`).
- Aparte jeugd-tarieven; overige regels als L1 regulier.
- SNOOP levert dit als **eigen export**, met `Werkgever op datum shift` =
  `Level One Payroll Jeugd`. Die wordt als `L1_JEUGD` verwerkt en dus tegen de
  jeugd-tariefkaart afgerekend. Alleen wanneer één export zowel regulier als
  jeugd bevat, geldt de export als `L1`.

### 5.4 Sterk Werk (`SW`)
- **Code-mapping:** `"B2 Sw"` → `B2` (suffix strippen).
- **Tariefkolommen:** `100/135` (samen) | `150` | `200` | `feestdag` |
  `50% nacht` | `Totaal nachtuur`.
- **Bedrag** = `(u0 + u35)·t100 + u50·t150 + unacht·t_totaalnachtuur + u100·t200`.
- **Conventies:** dag-grens **niet** factureren; feestdag als **150%-toeslag**
  boeken (op 150%-tarief, niet op apart feestdag-tarief); pauze op de **hoogste**
  toeslag; nachtdiensturen (rood in Nitea-Excel) op het `Totaal nachtuur`-tarief.

### 5.5 Cervokordaat (`CK`) — indien van toepassing
- **Code-mapping:** identity (`"C4"` → `C4`).
- `100%` en `135%` zijn **aparte** kolommen → overwerk op 135-tarief, basisuren
  op 100-tarief.
- Geen nachtdiensten; feestdag op apart `150%2`-tarief.

## 6. Tariefkaart in de applicatie (CAO-/minimumloon-wijzigingen)

De tariefkaart wordt **niet ingevoerd maar afgeleid**. Er wordt alleen een
**CAO-loontabel** geüpload; de tarieven volgen daaruit:

```
tarief = CAO-uurloon x omrekenfactor
```

- De **omrekenfactor** ligt contractueel vast met het uitzendbureau (per
  kaartschaal en per tariefcategorie) en verandert niet mee met de CAO.
- Een geüploade **loontabel** heeft een **`ingangsdatum`**. Vanaf die datum
  worden de tarieven tegen die lonen berekend; daarvóór blijft de vorige tabel
  gelden. Historische weken blijven dus kloppen.
- Bij een CAO-ronde uploadt de gebruiker dus **alleen de nieuwe lonen** — geen
  tarieven overtypen, geen codewijziging.

### 6.1 Per dag, niet per week
Een ingangsdatum valt zelden op een maandag: in 2026 is 1 juli een woensdag en
1 augustus een zaterdag. De tariefkaart wordt daarom **per dag** bepaald, niet
per week. Loopt er een ingangsdatum door de week heen, dan worden de minuten per
tariefperiode geteld en tegen het tarief van díe periode afgerekend; één
categorie levert dan twee regels op met elk hun eigen tarief en ingangsdatum.
De kwartier-afronding loopt over alle perioden tegelijk, zodat het weektotaal
gelijk blijft aan de Nitea-uren (§4).

Gecontroleerd op week 27/2026 (ma 29-06 t/m zo 05-07), Level One B2 Flex,
4 × 7,5 uur: 15 uur × € 28,94 (tabel 01-01) + 15 uur × € 29,49 (tabel 01-07)
= **€ 876,45**. Vóór deze wijziging liep de hele week op € 28,94.

### 6.2 Een tabel overschrijft alleen wat hij noemt
Loontabellen **stapelen per schaal**. Gaat er per 01-07-2026 alleen voor B1 en
B2 iets omhoog (het wettelijk minimumloon), dan hoeft die tabel alleen die twee
schalen te bevatten; de rest houdt het loon uit de laatste tabel die ze wél
noemde. Zonder die opbouw zouden alle niet-genoemde schalen vanaf die datum
zonder loon — en dus zonder tarief — komen te zitten, wat een halve week
stilzwijgend op nul zou zetten.

### 6.3 Trede 1 volgt trede 2
De CAO-loontabel laat de regel voor trede 1 leeg. Wie daarop staat wordt gelijk
aan trede 2 beloond (opgave Kwekerij Baas, augustus 2026), dus `B1` valt terug
op `B2`. Zonder die terugval zou een uitzendkracht op B1 zonder loon en dus
zonder tarief komen te zitten.

### 6.4 Een gedeeltelijke tariefexport sluit de rest niet af
Level One levert soms alleen de gewijzigde schalen (de export per 01-07-2026
bevatte B2 en B3, tegenover 99 kaartcodes). De omrekenfactoren die er niet in
staan lopen daarom door in plaats van te worden afgesloten; alleen de
combinaties in de upload worden vervangen. Het verschiloverzicht vergelijkt met
de factoren zoals ze ná de upload gelden, zodat ongemoeide schalen niet ten
onrechte als 'vervallen' worden gemeld.

### 6.5 Handmatige tarieven
Voor kaartcodes die op de kaart van het bureau ontbreken (de Level One-kaart
mist de E-schalen) kan op de Lonen & tarieven-pagina per kaartcode en categorie
een **handmatig tarief** worden ingevoerd, met ingangsdatum (SCD2); wie het
invoert wordt vastgelegd en getoond. Handmatig **wint** van de afgeleide kaart
en telt mee in de schaal-toets bij Uitzendkrachten. Let op: een handmatig
tarief beweegt **niet** mee met een CAO-loonronde — bij elke nieuwe loontabel
de lijst nalopen, en de regel verwijderen zodra het bureau een kaart levert
waar de schaal wél op staat.

Brengt een **import** (tariefkaart of Level One-export) een kaartcode mee
waarvoor een handmatig tarief nog loopt, dan vraagt het resultaatscherm **per
schaal ja of nee**, met de naam van de invuller erbij. Ja beëindigt het
handmatige tarief per de ingangsdatum van de import (oude weken houden het);
daarna overschrijven volgende imports geruisloos. Nee laat het handmatige
tarief vóórgaan, en de vraag komt bij een volgende import terug. De Lonen &
tarieven-pagina toont daarnaast alle **ingeladen loontabellen** met de
markering welke actueel is, zodat te zien is wat er al staat vóórdat iemand
een tabel (nogmaals) uploadt.

Een loontabel-upload met een **al bestaande ingangsdatum** vervangt die tabel
volledig; laat de upload schalen verdwijnen (half ingelezen bestand), dan
meldt het resultaatscherm welke. De CAO-PDF van 01-01-2026 werd zo maar voor
28 van de 87 schalen ingelezen; de overige 54 zijn op 19-08-2026 hersteld.

### 6.6 De ingangsdatum komt uit het bestand
De datum wordt niet overgetypt maar afgelezen: uit de kolomkop van de Level
One-export en uit de tekst van de CAO-PDF. Level One schrijft die kop op twee
manieren — `Loon per 1/7/26` en `Loon per 1 jul`. Bij die tweede ontbreekt het
jaartal; dan wordt van vorig, dit en volgend jaar het dichtstbijzijnde gekozen
en de uitkomst op het scherm getoond, zodat een verkeerde gok opvalt vóór er
weken mee worden verwerkt. Het invulveld in het scherm blijft als terugval.

De kolommen `Code`, `Component` en `Percentage` worden in de kopregel opgezocht
in plaats van op een vaste plek verwacht: Level One levert de export niet altijd
met evenveel lege tussenkolommen, en op een vaste positie rekenen leverde een
bestand op waarin geen enkele tariefregel werd gevonden.

**Waarom een factor en niet een tarieventabel:** de verhouding
`tarief / uurloon` is per uitzendbureau stabiel. Gemeten op de kaart per
01-01-2026: bij Sterk Werk is de verhouding tussen de categorieën constant over
alle schalen (150% = 1,1075 × 100%; nachtuur = 1,364 ×; 200% = 1,487 ×), bij
Level One constant binnen een schaaltype (Flex ≈ 1,172; Vast ≈ 1,151).

**Datamodel:** `cao_loontabel` + `cao_loon` (de upload) en `uzb_tarief_factor`
(SCD2, per UZB × kaartcode × categorie). Een kaartcode verwijst naar een
CAO-schaal: `B4F` (Flex) en `B4V` (Vast) delen het CAO-loon van schaal `B4`
maar hebben een eigen factor.

**Bootstrappen:** `leid_factoren_af()` berekent de factoren eenmalig uit de
huidige, met de uitzendbureaus afgestemde tariefkaart (`factor = tarief /
uurloon`). Daarna volstaan loontabel-uploads.

**Twee soorten upload:**

| Upload | Wanneer | Gevolg |
|---|---|---|
| **CAO-loontabel** | Loonronde | Nieuwe lonen vanaf ingangsdatum; factoren blijven, tarieven bewegen mee |
| **UZB-tariefkaart** | Onderhandeling / nieuwe kaart van het bureau | Factoren opnieuw afgeleid (`tarief ÷ loon`) vanaf ingangsdatum |

De omrekenfactor wordt dus **nooit met de hand ingevoerd**; hij volgt uit het
brondocument dat het uitzendbureau aanlevert. Een losse handmatige correctie
per schaal/categorie blijft mogelijk (met ingangsdatum en toelichting), maar is
de uitzondering.

**Uniforme factor:** Sterk Werk en Cervokordaat hanteren contractueel één
factor per tariefcategorie voor álle schalen; Level One verschilt per
suffix (Vast/Flex/Seizoens). Dat staat als `uniforme_factor` per tabblad in
`services/ingest/tariefkaart.py`, zodat een afwijkende schaal automatisch wordt
gesignaleerd.

**Validatie bij upload** (`services/tarief/validatie.py`):
- **uitschieters** — per categorie de verhouding tot het basistarief vergeleken
  met de mediaan over alle schalen; >5% afwijking is vrijwel altijd een
  typefout of kapotte formule. Jeugd- en volwassenschalen worden apart
  vergeleken (ze hebben eigen verhoudingen);
- **gaten** — een categorie die voor de meerderheid van de schalen geldt maar
  bij deze schaal ontbreekt;
- **niet-uniforme factor** bij bureaus die één factor per categorie hanteren;
- **onder het minimumloon**;
- **verschiloverzicht** — wat de omrekenfactoren doen t.o.v. de vorige versie,
  zodat een onbedoelde wijziging opvalt vóór bevestiging.

Op de kaart per 01-01-2026 leverde dit drie fouten op die in het Excel-bestand
onopgemerkt waren gebleven: Cervokordaat C3 met een 135%-tarief van € 29,80
(lager dan het basistarief; verwacht ~€ 38,36), Level One jeugd-payroll B3 met
een 200%-tarief van € 40,91 (verwacht ~€ 38,39), en schaal 18C2 zonder 150%- en
200%-tarief.

> **Implementatie:** `services/ingest/loontabel.py` en
> `services/ingest/tariefkaart.py` (uploads), `services/tarief/kaart.py`
> (afleiden, factor-bootstrap, keuze op datum) en
> `services/tarief/validatie.py` (controles + verschiloverzicht).

## 7. Factuurcontrole (reconciliatie)

Match elke factuurregel op medewerker (voor- **en** achternaam; let op dubbele
achternamen — bv. drie × "Grasu" — desnoods op bedrag disambigueren). Vergelijk
per medewerker **uren** en **bedrag**. Classificeer afwijkingen:

| Categorie | Betekenis | Actie |
|---|---|---|
| **Uren-afwijking** | Gefactureerde uren ≠ Nitea-uren | Uitzoeken: nafactuur of telfout |
| **Tarief-afwijking** | Factuurtarief ≠ tariefkaart voor die schaal | Loonschaal in SNOOP verifiëren |
| **Toeslag-classificatie** | Wij 50% vs UZB 135% (of omgekeerd) | Eenmalig afstemmen met UZB |
| **Afronding** | UZB draagt 3 decimalen (bv. €28,942 vs €28,94) | Ruis; negeren |

**Uitsluiten van de berekening:** medewerkers zonder geldige tarief-code (leeg in
SNOOP / niet in tariefkaart) worden niet meegerekend en apart gerapporteerd —
anders vertekent een €0-tarief het gemiddelde (bevinding "Bedrag niet te
controleren (geen tarief bij ons)", zie §4).

**Apart gefactureerden** (§4) krijgen een eigen controle: de facturen worden
als één geheel gekoppeld (de aparte factuur zit er meestal gewoon tussen),
daarna wordt per deel vergeleken. Factuurregels die aan niemand te koppelen
zijn, horen bij het hoofddeel. Het resultaatscherm toont per deel de
samenvatting, de bevindingen met actie, het matchingsbestand en de
concept-bevindingenmail.

## 8. Referentie-uitkomsten (week 25/2026, ter regressietest)

Gebruik deze waarden als regressietest bij herbouw van de engine:

| UZB | Medewerkers | Uren | Netto bedrag |
|---|---|---|---|
| Level One regulier | 21 | 779,00 | € 23.822,59 |
| Sterk Werk | 29 | 861,00 | € 25.905,03 |
| L1 Volwassenen (Bednorz) | 1 | 20,25 | tarief HR-onbevestigd |
| L1 Jeugd | 7 | 69,00 | jeugd-tarief |

Effectief gemiddeld inkooptarief (incl. alle toeslaguren), L1 excl. jeugd +
Sterk Werk samen: **€ 30,34/uur**.

Factuurcontrole week 25 (samenvatting): Sterk Werk uren exact kloppend
(861,00 u, +€ 11,48 op bedrag = 0,04 %); Level One regulier −€ 414 (in ons
voordeel) met twee uren-gaten (Janicki −6 u, Machura −4 u) en één
loonschaal-afwijking (Girtoi B2 vs C2).
