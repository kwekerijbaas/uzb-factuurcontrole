# Uren-controle — Level One Uitzendbureau (Fase 1)

Vergelijkt wekelijks de doorgegeven uren (Excel) met de PDF-facturen van Level One
Uitzendbureau B.V. Output: één Excel-rapport en één tekstuele mail-concept.

## Snelstart

```bash
pip install -r requirements.txt
python main.py
```

Zonder argumenten: het programma scant `~/Downloads/`, toont een overzicht van alle
weken met hun status (compleet / wacht op facturen / alleen factuur), en kiest
**automatisch de meest recente complete week** (Excel + ≥1 factuur). Output gaat
ook naar `~/Downloads/`.

Met expliciete week:
```bash
python main.py --week 15 --jaar 2026
```
Dan wordt die exacte week verwerkt, ook als hij niet compleet is — dan krijg je
een duidelijke melding wat ontbreekt.

Voorbeeld inventaris-output:
```
  Week     Excel    Facturen   Status
  ----     -----    --------   ------
  18/2026  ✓        0          wacht op facturen
  17/2026  ✓        0          wacht op facturen
  15/2026  ✓        3          compleet            ← deze wordt gekozen
  04/2026  ✓        4          compleet
```

⚠️ `.xls` (oud Excel-formaat) wordt nog niet ondersteund — converteer naar `.xlsx`
of gebruik nieuwere Nitea-export.

## Wat het programma doet

1. Leest `~/Downloads/WK <nr> L1.xlsx` met de doorgegeven uren per medewerker per dag.
2. Leest alle `~/Downloads/PP_IFAC*Level One Uitzendbureau B.V.*PurchaseInvoice.pdf`
   en filtert op de juiste week.
3. Matcht factuurnamen op Excel-namen (achternaam + voornaam tussen haakjes).
4. Vergelijkt totaal-uren per medewerker tegen de drempel uit `config/drempels.yaml`.
5. Valideert het tarief van elke factuurregel tegen `config/tarieven_uzb.xlsx`
   (tabblad L1, met `geldig_vanaf`/`geldig_tot` voor historie).
6. Schrijft een Excel-rapport met 5 tabbladen + een mail-concept als `.txt`.

## Output

- `Rapportage_Urencontrole_L1_WK<nr>_<jaar>.xlsx`:
  1. **Samenvatting** — totalen + telling per status + financiële impact
  2. **Detail per medewerker** — alle medewerkers + splitsing 100/135/150/200/Bijz + loonschaal + tarief
  3. **Aandachtspunten** — geel: contractvorm-switch, tarief-afwijking, ongebruikelijke categorie, niet op factuur
  4. **Te veel gefactureerd** — rood: medewerkers/uren niet in Excel + financiële impact
  5. **Werkwijze** — herhaalbare instructies + legenda
- `Mail_concept_L1_WK<nr>_<jaar>.txt` — kopiëren in Outlook voor verzending naar Level One

## Statussen

| Status | Kleur | Betekenis |
|---|---|---|
| ✓ OK | groen | Uren én tarief kloppen |
| ⚠ Uren afwijking | geel | Totaal uren factuur ≠ Excel (>0,05u verschil, configureerbaar) |
| ⚠ Tarief afwijking | oranje | Tarief op factuur niet in tarieventabel |
| ⇄ Contractvorm switch | oranje | Schaal of contractvorm wisselt binnen één week |
| ⚡ Ongebruikelijke categorie | geel | 200%, bijz 150%, bereikbaarheid, reiskosten of correctie |
| ○ Niet op factuur | blauw | Doorgegeven, niet gefactureerd — mogelijk Payroll (buiten scope) |
| ✗ TE VEEL GEFACTUREERD | hard rood | Op factuur, niet doorgegeven — credit aanvragen |

## Configuratie

### Drempels — `config/drempels.yaml`

```yaml
totaal_uren_drempel: 0.05    # 3 minuten
tarief_drempel: 0.02         # €/uur
splitsing_drempel: 0.25      # alleen rapporteren, niet flaggen
financiele_drempel_eur: 5.00
```

Tweak in deze YAML zonder code aan te raken.

### Tarieven — `config/tarieven_uzb.xlsx`

Eén tabblad per UZB (`L1`, en later `SW`, `CK`). Per regel:

| loonschaal | contractvorm | basisloon | tarief_100 | tarief_135 | tarief_150 | tarief_200 | tarief_bijz_150 | geldig_vanaf | geldig_tot | opmerking |
|---|---|---|---|---|---|---|---|---|---|---|

**Bij tariefswijziging** (CAO/wettelijk/onderling):
1. Open `config/tarieven_uzb.xlsx` → tabblad L1
2. Vul `geldig_tot` in voor alle nu-actieve rijen (bv. `2026-04-30`)
3. Voeg nieuwe rijen toe met `geldig_vanaf` (bv. `2026-05-01`)

**Of** automatisch: als LO een nieuwe `tarieven 2026 Level One.xlsx` stuurt:
```bash
python config/_genereer_tarieven_uzb.py "~/Downloads/<nieuwe-tarievenlijst>.xlsx" L1 --vanaf 2026-05-01
```
Dat sluit oude rijen automatisch af en voegt nieuwe toe.

## Architectuur

```
uren_controle/
  main.py                          CLI entry point
  parsers/
    excel_doorgegeven.py           WK XX L1.xlsx → DoorgegevenWeek
    factuur_l1.py                  L1 PDF → FactuurL1
    tarieven.py                    tarieven_uzb.xlsx → TarievenDatabase
  matching/
    naam_matcher.py                "I.A. Baldowska (Iwona)" ↔ "Iwona Baldowska"
    valideer.py                    alle controle-regels → MatchResultaat
  output/
    rapport_excel.py               5-tabblad Excel
    mail_concept.py                .txt mail naar UZB
  config/
    drempels.yaml                  tweakbaar
    tarieven_uzb.xlsx              beheerd door planner
    _genereer_tarieven_uzb.py      hulp-script bij tariefupdate
  README.md
  requirements.txt
```

## Toekomst — Fase 2 en 3

- **Fase 2**: Nitea PDF parser toevoegen om de Excel automatisch te genereren uit
  kloktijden. Bestaande analyse-scripts in `../analyse/` vormen het uitgangspunt.
- **Fase 3**: Planningsbestanden + afrondingsregels + automatische e-mail naar UZB
  na review/correctie door arbeidsplanner.

Beide fases hergebruiken `factuur_l1.py`, `tarieven.py`, `valideer.py` en de
output-modules ongewijzigd.

## Beperkingen Fase 1

- Alleen **Level One Uitzendbureau B.V.** (geen Payroll, geen SW/CK).
- Doorgegeven uren moeten in `WK <nr> L1.xlsx` staan, formaat zoals in week 15 2026.
- Splitsing 100/135/150 wordt alleen gerapporteerd, niet zelf herrekend — de
  rekenregel die LO toepast is niet eenduidig reproduceerbaar uit deze data.
- Tarief-afwijkingen worden gevlagd, maar niet automatisch afgekeurd: jij beslist
  of een afwijking een echte fout is of een legitieme nieuwe schaal.

## Testresultaat week 15 2026

Op de meegeleverde data (`WK 15 L1.xlsx` + 3 PDF-facturen):

| | |
|---|---|
| Doorgegeven uren | 2912,25u |
| Gefactureerde uren | 2889,25u |
| Verschil | -23,00u (in ons voordeel) |
| Status OK | 78 medewerkers |
| Uren-afwijking | 1 (Iryna Ilchuk -7u, in ons voordeel) |
| Contractvorm-switch | 4 (3× B3 Seizoen → B4 Flex; 1× E5 Flex → E5 Vast) |
| Niet op factuur | 2 (Viktoriia Terlieieva 7,25u; Bohdan Perlivanov 8,75u) |
| Te veel gefactureerd | 0 — geen credit-aanvraag nodig |
