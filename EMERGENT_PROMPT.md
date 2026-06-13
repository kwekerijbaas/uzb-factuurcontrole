# Emergent.sh build-prompt — "UZB Toeslag- & Factureerbaar-app" (mobiel)

> **Repo (reference implementation):** `https://github.com/kwekerijbaas/verkoop-automatisering`
> map **`/uzb-factuurcontrole`** bevat de bewezen Python-engine (`controle_uzb.py`, `toeslag.py`,
> `bureau_profielen.py`, `snoop.py`, `tijd_afronding.py`). Port de parsing- en CAO-rekenlogica daaruit
> 1-op-1; de regels staan hieronder óók expliciet zodat je het resultaat kunt verifiëren. Bij twijfel
> is de repo leidend. (De `.py`-bestanden bevatten nog hardcoded Windows-paden — parametriseer die
> naar uploads/omgevingsvariabelen in de backend.)

---

## 1. Doel

Bouw een **native mobiele app (React Native / Expo, iOS + Android)** voor Kwekerij Baas
— een potplantenkwekerij die met meerdere uitzendbureaus (UZB) werkt. De app rekent uit de
**werkelijke kloktijden** (Nitea) per uitzendkracht (UZK) de **CAO-conforme urenverdeling over
toeslag-categorieën** uit, en toont per UZB **direct wat zij maximaal mogen factureren per
toeslag-uur**.

De zware rekenlogica (PDF/Excel-parsing + CAO-berekening) draait **server-side** in een Python
(FastAPI) backend; de Expo-app is de client die uploadt en de resultaten toont.

De app vervangt geen boekhouding; hij geeft de finance-/HR-medewerker onderweg op de telefoon in
één oogopslag: "klopt wat dit UZB straks gaat factureren?".

**UI-taal = Nederlands.** Alle labels, knoppen en kolomkoppen in het Nederlands.

---

## 2. Bron-data (uploads)

De gebruiker uploadt per week (of selecteert uit eerder geüploade bestanden):

1. **Nitea-overzicht (PDF)** — werkelijke start-/eindtijden per medewerker per dag.
   Regelformaat per dag: `<volgnr> <tag> - <naam> <dd-mm-jjjj> <begin HH:MM> <eind HH:MM> <gewerkt HH:MM> <pauze HH:MM>`.
   Soms ontbreekt begin of eind (nachtdienst). Tag = Nitea-tagnummer, Naam = medewerker.

2. **Nitea-Excel "doorgegeven uren" (xls/xlsx)** — per medewerker per week: kolom "Medewerkers"
   met `<tag> <naam>`, daarna dag-kolommen Ma..Zo, dan "Totaal". Bestandsnaam bevat week + bureau-hint
   (`WK 15 L1.xlsx`, `WK16 KT.xlsx`, `WK 09 Jeugd.xlsx`, `WK 12 SW.xls`).

3. **SNOOP-export (xlsx)** — tab `tablelist_qryomordrlne`, kolommen: Registratienummer, Medewerker,
   Datum, Starttijd, Eindtijd, Werkelijke starttijd, Werkelijke eindtijd, Gewerkte uren, Locatie,
   `Werkgever op datum shift` (= het UZB), Type uitzendkracht, `Tarief uitzendbureau` (= de **inschaling/schaal**, bv. `B2`, `B3 Flex`, `15B2`).
   - Medewerkers van **Kwekerij Baas** en **Temper** NIET meenemen (eigen personeel).
   - Schaal is **tijd-afhankelijk** (zie §6): pak per factuurweek de schaal die op die datum gold.

4. **Tariefkaart per UZB (xlsx)** — per UZB een tabblad met per **schaal** het tarief per categorie.
   Kolomindeling verschilt per UZB (zie §5). Lever ook een per-medewerker variant (op tagnummer)
   als fallback wanneer SNOOP geen schaal heeft.

De vier bureaus en hun codes/hints:
| Code | UZB | Bestand-hint |
|------|-----|--------------|
| L1 | Level One Uitzendbureau B.V. | `L1` |
| LP | Level One Payroll bv (Jeugd + Volwassen) | `Jeugd`, `PL`, `Volwassen` |
| SW | Sterk Werk Uitzendburo B.V. | `SW` |
| CK | CervoKordaat / Workstead (Kordaat Agri) | `KT` |

---

## 3. CAO-rekenlogica (Glastuinbouw jaarurenmodel) — EXACT overnemen

Per medewerker, per week, op basis van de Nitea-kloktijden:

**Afronding:** begin- en eindtijd afronden op het dichtstbijzijnde **hele of halve uur**.

**Pauze:** een 12-uurs nachtshift heeft in totaal **1 uur 15 min** pauze. Gewerkte uren = netto.

**Dag- en weekstaffel (alleen ma–vr tellen mee voor de staffel):**
1. Per weekdag: uren **boven 10u/dag** → **150%** (`dag_ow`).
2. De rest (per dag gemaximeerd op 10u) wordt gesommeerd tot **gecorrigeerde weekuren**.
3. Staffel op die gecorrigeerde weekuren (normweek = **38u**):
   - ≤ 38u → **100%**
   - 38–48u → eerste 38u = 100%, deel 38–48 = **135%** (overwerk)
   - > 48u → 38u=100%, 10u=135%, rest = **150%**
4. Tel de `dag_ow` (>10u/dag) op bij **150%**.

**Toeslagvensters (supplementen, bovenop bovenstaande):**
- **Nacht +50%** (Sub III): Ma–Vr in venster **00:00–06:00** en **20:00–24:00**.
  (Let op: 18:00–20:00 = gewoon 100%, géén nacht.)
- **Zaterdag +50%:** venster **00:00–06:00** en **15:00–24:00** telt als nacht-supplement;
  overige zaterdaguren als **za-uren** (apart tonen).
- **Zondag +100%:** alle zondaguren.

**Nachtdienst-detectie** (dan geldt aanname-shift **18:00–06:00**, 12u):
- geen begin én geen eind in Nitea, OF
- eind < begin (over middernacht), OF
- begin ≥ 18:00.
Voor een doorlopende nachtdienst-tussendag (geen begin/eind): ~**10/12** van de uren in nacht-venster.

**Output per medewerker** (zoals `bereken_toeslag` in de repo):
`totaal, is_nachtdienst, c100, c135, c150, nacht, za, zo`
(c100/c135/c150 = uren in die staffel; nacht/za/zo = supplement-uren).

> ⚠️ Verschillende UZB's tellen overwerk/zaterdag soms nét anders. Behandel deze CAO-berekening als
> de **norm** ("dit mág gefactureerd worden"); de app vergelijkt de UZB-factuur hier later tegen.

---

## 4. De twee outputs (kern van de app)

### Output A — Gedetailleerde urenverdeling (drill-down, mobiel uitklapbaar)
Hiërarchie, elk niveau inklap-/uitklapbaar:

```
UZB  (L1 / LP / SW / CK)
 └─ UZK (uitzendkracht, met tag + schaal)
     └─ per dag (ma..zo): begin–eind, gewerkte uren
         └─ urenverdeling: 100% | 135% | 150% | nacht+50% | za+50% | zo+100%
```
Per UZK een weektotaal-regel met de som per categorie. Per UZB een bureautotaal.

### Output B — "Mag factureren" per UZB (de hoofd-deliverable)
Per UZB één compact overzicht dat **direct toont wat dat UZB maximaal mag factureren**, uitgesplitst
**per toeslag-categorie**:

| Categorie | Uren (UZB-totaal) | Tarief €/u (volgens schaal) | Bedrag € |
|-----------|------------------:|----------------------------:|---------:|
| 100% normaal | … | … | … |
| 135% overwerk | … | … | … |
| 150% overwerk/bijzonder | … | … | … |
| Nacht +50% | … | … | … |
| Zaterdag +50% | … | … | … |
| Zondag +100% | … | … | … |
| **Totaal** | | | **€ …** |

- Tarief per categorie komt uit de **schaal** van die UZK (SNOOP, tijd-correct) × de UZB-tariefkaart.
- Omdat verschillende UZK's verschillende schalen hebben, is "Uren × tarief" **per UZK** berekend en
  daarna **per categorie per UZB** opgeteld (toon ook het per-UZK detail onder de UZB-regel).
- Toon het **eindbedrag per UZB** groot en bovenaan ("Dit mag UZB X deze week factureren: € …").
- Tarief-vergelijking op **2 decimalen** (factuur 3 dec vs schaal 2 dec → gelijk bij 2 dec = akkoord).

**Export/deel:** elke output deelbaar als PDF en Excel, en een platte tekst die je kunt kopiëren/mailen.

---

## 5. Tarief-categorieën per UZB (mapping factuurcategorie → tariefkaart-kolom)

| UZB | normaal/135% | 150% | nacht/bijzonder |
|-----|--------------|------|------------------|
| L1  | `100%/135%` | `150%` | `150% bijzonder ; 50% nachturen` |
| LP  | `100%` | `150%` | (bijzonder = 150%-kolom) |
| SW  | `100%/135%` | `150%` | `50% nacht` |
| CK  | `100%` (norm), `135%` (apart), `150%` | `150%` | — |

Tariefkaart-tabbladen (namen in de werkbestanden):
- L1: `Werknemers+schaal+tarief` (kolommen 100%/135%, 150%, 200%, "150% bijzonder ; 50% nachturen")
- LP: `Werknemers+schaal+tarief` (100%, 150%, 200%)
- SW: `Medewerkers+schaal+tarief` (100%/135%, 150%, 200%, Feestdag, 50% nacht)
- CK: `Werknemers+tarief+schaal` (100%, 135%, 150%, 200%)
Per-medewerker key = kolom **Nummer** (= tagnummer), met Voornaam, Achternaam, Schaal.

---

## 6. Belangrijke bedrijfsregels (NIET weglaten)

1. **Schaal is tijd-afhankelijk.** Een medewerker kan halverwege het jaar een schaal omhoog gaan.
   Pak voor een factuurweek de schaal die **op die datum** in SNOOP gold (meest voorkomende in die
   week → laatste vóór → eerste na). Niet zomaar de modus over het hele jaar.
2. **Tagnummers zijn uniek per DAG, niet over tijd.** Nitea hergebruikt tagnummers (eindig aantal):
   dezelfde tag mag in verschillende weken/dagen bij verschillende personen horen — dat is normaal.
   Een **echt conflict** is alleen: zelfde tag, **zelfde dag**, twee personen. Personen zijn uniek in
   SNOOP op **naam**. Koppel UZK's per week op **naam** (niet blind op tag).
3. **Eigen personeel uitsluiten:** Kwekerij Baas + Temper niet meenemen.
4. **Geen schaal in SNOOP?** Val terug op de per-medewerker tariefkaart (op tag, mits de **achternaam
   klopt** — anders op naam), of toon "UZB-tarief / inschaling onbekend" i.p.v. te gokken.
5. **Te veel factureren is het risico.** Als de berekende ("mag-factureren") uren/bedragen lager zijn
   dan wat het UZB straks factureert, is dat een harde afwijking (markeer rood). Te weinig = laag risico.
6. **Reiskosten** = km × tarief/km; GEEN gewerkte uren. Niet in de uren-toeslag meenemen.

---

## 7. Tech & UX

**Architectuur:** native client + Python API.
- **Frontend:** **React Native via Expo** (iOS + Android, één codebase). `expo-document-picker` /
  share-sheet om Nitea-PDF, SNOOP- en tariefkaart-bestanden vanaf de telefoon (of cloud) te kiezen
  en te uploaden. Grote tap-targets, uitklapbare lijsten (accordion), sticky UZB-totaalbalk bovenaan,
  pull-to-refresh. Lokale cache (AsyncStorage) van de laatst berekende week voor offline inzien.
- **Backend:** **Python FastAPI** die de bewezen engine-logica uit de repo host als REST-API
  (pdfplumber voor PDF, openpyxl/pandas voor Excel). Endpoints minimaal:
  `POST /upload` (bestanden), `POST /bereken?week=` (draait CAO-berekening), `GET /uzb/{code}`
  (Output A + B als JSON), `GET /export/{code}?formaat=pdf|xlsx`. App praat alleen met deze API.
- **Flow:** (1) week kiezen / bestanden uploaden → (2) "Bereken" → (3) twee tabs: **"Urenverdeling"**
  (Output A) en **"Mag factureren"** (Output B), met een UZB-keuzebalk bovenaan.
- **Robuust tegen formaatverschillen** tussen de 4 UZB-factuurlayouts en oude `.xls` vs nieuwe `.xlsx`.
- Toon altijd brondatum/weeknummer en een "ververs"-knop. Bedragen NL-notatie (€ 1.234,56).
- **Auth:** simpele login (kwekerij-intern, bv. e-mail + pincode), geen publieke toegang. API met token.
- **Delen:** native share-sheet voor de PDF/Excel/tekst-export (Output A en B).

---

## 8. Acceptatiecriteria

- Upload van een Nitea-PDF + SNOOP + tariefkaart voor één week levert beide outputs correct.
- Een UZK met een 18:00–06:00 nachtshift toont nacht+50%-uren > 0 en is als nachtdienst gemarkeerd.
- Een UZK met > 48 weekuren toont 100/135/150 correct verdeeld; > 10u op één dag → 150%.
- Per UZB klopt het eindbedrag met de som van (uren per categorie × schaaltarief) over alle UZK's.
- Schaal-wisselaar (bv. B2 t/m wk21, C4 vanaf wk22) krijgt per week de juiste schaal.
- Zelfde tag bij twee personen op verschillende dagen → géén conflict; zelfde dag → wél gemarkeerd.
- Output A en B exporteerbaar als PDF/Excel en kopieerbare tekst.

---

## 9. Wat NIET in scope (deze versie)

- Geen automatische koppeling met Blue10/boekhouding.
- Geen schrijftoegang naar SNOOP of de HR-werkbestanden (alleen lezen/uploaden).
- Geen credit-/correctiefactuur-afhandeling (dat blijft in de desktop-engine).
