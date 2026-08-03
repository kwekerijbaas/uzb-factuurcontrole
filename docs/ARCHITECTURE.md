# Architectuur & deploy — uf.kwekerijbaas.nl

De UZB-urencontrole wordt een beveiligde webapp waar Ola en Jacob wekelijks de
SNOOP- (Excel) en Nitea- (PDF) bestanden inladen en de urenoverzichten +
factuurcontrole terugkrijgen.

## 1. Opzet in het kort

```
Browser (Ola/Jacob)
   │  HTTPS, ingelogd via Entra ID
   ▼
uf.kwekerijbaas.nl  ──CNAME──►  Azure App Service (Linux, Python)
   │
   ├── backend/    FastAPI + engine (calc) + datamodel (SCD2-versiebeheer)
   ├── frontend/   upload → resultaat → downloads   (nog te bouwen)
   └── Azure Postgres + Blob Storage
         ├── loonschaal/toeslag_regel  (SCD2: geldig_van/geldig_tot)
         └── runs/                      (verwerkte weken + overzichten)
```

**Repo-structuur (branch `claude/staffing-hours-app-z19SH`):**
`backend/` (datamodel, migraties, calc-engine, tests), `docs/` (deze docs +
CAO-bron), `frontend/` (nog te bouwen). De bestaande `artikelinvoer`-app in de
repo-root staat hier los van.

> **Versiebeheer van tarieven** is al in het datamodel verankerd via een
> SCD2-patroon (`geldig_van`/`geldig_tot` op `loonschaal` en `toeslag_regel`) —
> dit dekt de CAO-/minimumloonwijzigingen uit SPEC §6.

**Waarom App Service (en niet een statische web app):** de app moet PDF's
inlezen en rekenen — dat vereist een server-side runtime (Python). De oude
Azure Static Web App volstond daar niet voor.

## 2. Componenten

| Component | Keuze | Toelichting |
|---|---|---|
| Runtime | **Azure App Service (Linux, Python 3.12)** of Container Apps | Draait de FastAPI-backend. Container Apps als je liever met de Dockerfile werkt. |
| Web framework | **FastAPI** + server-rendered HTML (Jinja) of een lichte SPA | Upload-formulier, resultaatpagina, downloads. |
| Auth | **Entra ID** via App Service Authentication ("Easy Auth") | Alleen aangewezen medewerkers. Geen eigen wachtwoordbeheer. |
| Opslag | **Azure Blob Storage** | Tariefkaart-versies + verwerkte weken. Loon-/tariefgegevens dus versleuteld at-rest, niet publiek. |
| Domein | `uf.kwekerijbaas.nl` via **CNAME** + managed TLS-certificaat | Zie §4. |
| CI/CD | **GitHub Actions** vanuit `kwekerijbaas/artikelinvoer` | Bouwt en deployt bij push naar de app-branch. |

## 3. Toegang (Entra ID)

- App Service Authentication aanzetten met de Microsoft Entra-provider.
- Toegang beperken tot een **beveiligingsgroep** (bv. `UF-gebruikers`) met
  Ola, Jacob en wie jij toevoegt — beheer je dan in Entra, niet in de app.
- App vereist login vóór elke request; geen anonieme toegang.

## 4. Custom domain + DNS

1. In App Service: **Custom domains → Add** `uf.kwekerijbaas.nl`.
2. Azure toont een verificatie-`TXT` en de doel-hostname voor de `CNAME`.
3. In het DNS-beheer van `kwekerijbaas.nl`:
   - `CNAME  uf  →  <app-name>.azurewebsites.net`
   - `TXT    asuid.uf  →  <verificatiewaarde>`
4. Terug in Azure: domein valideren en **managed certificate** aanzetten (gratis
   TLS, auto-verlenging).

> Provisionen van de Azure-resource en het zetten van de DNS-records doe jij
> (je gaf aan de rechten te hebben). De app + deploy-config lever ik klaar in de
> repo, inclusief de exacte stappen hierboven.

## 5. Geheimen & configuratie

- **Geen** secrets in de repo. App-instellingen (Blob-connectionstring, e.d.)
  via **App Service Configuration** of **Key Vault**.
- Lokale ontwikkeling via een `.env` (in `.gitignore`), met een
  `.env.example` als sjabloon.

## 6. Wekelijkse flow voor de gebruiker

1. Inloggen op `uf.kwekerijbaas.nl` (Entra).
2. Week kiezen; per UZB de **SNOOP (.xlsx)** en **Nitea (.pdf)** uploaden.
3. App genereert de **urenoverzichten** (download per UZB).
4. Optioneel: **UZB-factuur (.pdf)** uploaden → **factuurcontrole** met
   afwijkingen + concept-**bevindingenmail**.
5. Bij een CAO-/minimumloonwijziging: onder **Tariefkaarten** een nieuwe kaart
   met **ingangsdatum** uploaden (zie SPEC §6) — verder niets.

## 7. Bouwvolgorde & status

1. **Spec + architectuur** vastgelegd (`docs/`). ✅
2. **Datamodel + CAO-calculatie-engine + tests** (`backend/`). ✅ *(al aanwezig)*
3. **Ingest** — SNOOP (.xlsx) en Nitea (.pdf) inlezen (`services/ingest/`). ✅
4. **Tariefmapping per UZB + bedrag** (SPEC §5, `services/tarief/`). ✅
5. **Tariefkaart-beheer** — upload met ingangsdatum → SCD2-versie in de DB.
   *Vereist een modelwijziging: `loonschaal` moet per UZB en per
   tariefcategorie (100/135/150/200/feestdag/nachtuur), nu één `uurtarief`.* ⏳
6. **Web-laag** — upload/resultaat/downloads (FastAPI + frontend). ⏳
7. **Factuurcontrole** (SPEC §7) + bevindingenmail-generator. ⏳
8. **Deploy** — Dockerfile + GitHub Actions + Entra ID + custom domain. ⏳
