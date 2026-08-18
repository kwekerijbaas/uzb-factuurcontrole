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
| **Nitea-registratie** | `.pdf` (+ `.xlsx`) | Werkelijk gewerkte uren per medewerker per dag. **Nitea's "werk tijd" is leidend.** Nachtdiensturen zijn in de Nitea-Excel **rood** gemarkeerd. |
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

### Regels
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

### Loonschaal is verplicht
Een week wordt **niet verwerkt** zolang iemand die erin gewerkt heeft geen
loonschaal heeft. Zonder schaal is er geen tarief: de uren tellen dan wel mee
en het bedrag niet, waardoor het overzicht compleet lijkt terwijl het totaal te
laag is — en juist dat totaal gaat naast de factuur.

De schaal komt in volgorde uit (1) de SNOOP-regel van de week zelf en (2) de
laatst bekende schaal op de uitzendkracht. Die tweede is met de hand in te
vullen onder **Uitzendkrachten**, en wordt getoetst aan de tariefkaart die
vandaag geldt — een typefout zou anders alsnog een bedrag van nul opleveren.
Namen worden vóór de controle vastgelegd, zodat wie een schaal mist meteen op
die lijst staat.

In de praktijk komt dit vooral door een naamverschil tussen SNOOP en Nitea
(`Cristian` tegenover `Christian`, `Alex` tegenover `Alexander`): dan vindt de
koppeling de SNOOP-regel niet en blijft de schaal leeg.

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

### 6.5 De ingangsdatum komt uit het bestand
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
anders vertekent een €0-tarief het gemiddelde.

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
