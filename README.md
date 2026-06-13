# UZB-factuurcontrole — Kwekerij Baas

Systeem voor de **uren-, tarief- en toeslagcontrole** van uitzendbureau-facturen (UZB) bij Kwekerij
Baas. Vergelijkt de facturen van de uitzendbureaus met de intern doorgegeven uren (Nitea), de
inschaling/planning (SNOOP) en de afgesproken tarieven, en signaleert afwijkingen.

> ⚠️ **Geen klant-/bedrijfsdata in deze repo.** Nitea-PDF's, SNOOP-exports, facturen, tariefkaarten,
> rapportages en mailconcepten worden door `.gitignore` uitgesloten en horen hier NOOIT in.
> Alleen broncode + documentatie.

## Repo-inhoud

### Productie-engine (root)
| Bestand | Functie |
|---------|---------|
| `controle_uzb.py` | Hoofd-engine: facturen lezen, vergelijken, rapportages + werklijsten genereren. CLI: `python controle_uzb.py --map "<map met data>"`. |
| `bureau_profielen.py` | Per-UZB adapters (L1, LP, SW, CK): factuurformaat herkennen/parsen + tariefkaart-kolommen. |
| `snoop.py` | SNOOP-export lezen → per medewerker de (tijd-afhankelijke) inschaling/schaal. |
| `toeslag.py` | CAO Glastuinbouw jaarurenmodel: kloktijden → urenverdeling per toeslag-categorie. |
| `tijd_afronding.py` | Tijd afronden op heel/half uur. |
| `tim_mariska_uitwerking.py` | Inschaling-aansluiting + dag-bewuste tag-controle (voorbeeld-analyse). |

### App-build (root)
| Bestand | Functie |
|---------|---------|
| `EMERGENT_PROMPT.md` | Build-prompt voor de native mobiele app (opgemaakt). |
| `emergent_prompt_plakklaar.txt` | Zelfde prompt als platte tekst, plakklaar voor emergent.sh. |

### Bestaande webinterface (`uren_controle_dev/`)
Streamlit-app + losse parsers/matching/output-modules (eerdere desktop-UI). Code-only; de
tariefdata (`config/tarieven_uzb.xlsx`) wordt door `.gitignore` uitgesloten.

## Kernbegrippen (CAO + bedrijfsregels)

- **CAO-toeslag** (`toeslag.py` → `bereken_toeslag`): normweek 38u; staffel 100/135/150; >10u/dag = 150%;
  nacht +50% (Ma-Vr 20:00-06:00), za +50%, zo +100%; nachtdienst-aanname 18:00-06:00; afronden op half uur.
- **Schaal is tijd-afhankelijk** (`snoop_schaal_voor_week`): pak de schaal die in die factuurweek gold.
- **Tagnummers zijn uniek per DAG** (Nitea hergebruikt ze): conflict alleen bij zelfde tag, zelfde dag, 2 personen.
- **4 bureaus**: L1 (Level One Uitzendbureau), LP (Level One Payroll), SW (Sterk Werk), CK (CervoKordaat/Workstead).
- **Eigen personeel** (Kwekerij Baas + Temper) wordt uitgesloten.

## Let op

De `.py`-bestanden bevatten nog **hardcoded Windows-paden** (werkmap van de auteur). Voor een
server-/app-backend moeten die geparametriseerd worden naar uploads/omgevingsvariabelen.

## Installeren

```bash
pip install -r requirements.txt
```
