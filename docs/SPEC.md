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
| **SNOOP-export** | `.xlsx` | Per medewerker de **tarief-code** (loonschaal) en geplande/gewerkte uren. |
| **Nitea-registratie** | `.pdf` (+ `.xlsx`) | Werkelijk gewerkte uren per medewerker per dag. **Nitea's "werk tijd" is leidend.** Nachtdiensturen zijn in de Nitea-Excel **rood** gemarkeerd. |
| **Tariefkaart** | `.xlsx` | Per UZB een tabblad met per loonschaal de tarieven per toeslagcategorie. Heeft een **ingangsdatum** (zie §6). |
| **UZB-factuur** | `.pdf` | Voor de controlestap. Per UZB een eigen indeling (zie §5). |

## 3. Uitvoer

Per UZB een `.xlsx` met de tabbladen:
- **Tarieven** — de gebruikte tarieven (uit de geldende tariefkaart).
- **Per dag** — per medewerker per dag: begin/eind/pauze en uren per bucket + bedrag.
- **Totaal week** — weektotaal per medewerker per bucket + bedrag. *(Werkblad dat als eerste opent.)*
- **Afwijkingen** — aandachtspunten (bv. ontbrekende SNOOP-planning).

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

### 5.2 Level One — Volwassenen / Payroll (`L1_VW`)
- Payroll-schalen (bv. `C6 Payroll`). Factuur bevat naast loon soms een
  **reiskostenvergoeding** (aparte regel, aantal × tarief) die **niet** in de
  urenberekening zit maar wél op de factuur staat — apart benoemen in de controle.

### 5.3 Level One — Jeugd (`L1_JEUGD`)
- Jeugdschalen per leeftijd (bv. `B 15 jaar Jeugd`, `C2 18 jaar jeugd`).
- Aparte jeugd-tarieven; overige regels als L1 regulier.

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
- Bij het verwerken van een week wordt de loontabel gekozen die gold op de
  weekdatums (de laatste met `ingangsdatum ≤ weekmaandag`).
- Bij een CAO-ronde uploadt de gebruiker dus **alleen de nieuwe lonen** — geen
  tarieven overtypen, geen codewijziging.

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
