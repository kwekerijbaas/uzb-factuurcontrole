# Architectuur & deploy — uf.kwekerijbaas.nl

De UZB-urencontrole wordt een beveiligde webapp waar Ola en Jacob wekelijks de
SNOOP- (Excel) en Nitea- (PDF) bestanden inladen en de urenoverzichten +
factuurcontrole terugkrijgen.

## 1. Opzet in het kort

```
Browser (Ola/Jacob)
   │  HTTPS, ingelogd met een code per e-mail
   ▼
uf.kwekerijbaas.nl  ──CNAME──►  Render (Docker, regio Frankfurt)
   │                              backend/  FastAPI + calc-engine + templates
   │
   ├── Supabase Auth       inlogcode per e-mail, alleen @kwekerijbaas.nl
   └── Supabase Postgres   loontabellen, omrekenfactoren (SCD2), weekcontroles
```

**Waarom deze verdeling:** Supabase levert de database en het inloggen, maar
draait geen Python — Edge Functions zijn Deno/TypeScript. De app is Python
(FastAPI, met `pdfplumber` voor de Nitea-PDF's) en heeft dus een plek nodig die
containers draait. Render leest `render.yaml` en bouwt `backend/Dockerfile`;
Fly.io of Railway werken met dezelfde image.

**Repo-structuur (branch `claude/staffing-hours-app-z19SH`):**
`backend/` (datamodel, migraties, calc-engine, web-laag, tests), `docs/` (deze
docs + CAO-bron), `render.yaml` (deploy). De bestaande `artikelinvoer`-app in de
repo-root staat hier los van.

> **Versiebeheer van tarieven** is al in het datamodel verankerd via een
> SCD2-patroon (`geldig_van`/`geldig_tot` op `loonschaal` en `toeslag_regel`) —
> dit dekt de CAO-/minimumloonwijzigingen uit SPEC §6.

## 2. Componenten

| Component | Keuze | Toelichting |
|---|---|---|
| Runtime | **Render** (Docker, Frankfurt) | Bouwt `backend/Dockerfile` uit deze repo. Fly.io/Railway werken ook. |
| Web framework | **FastAPI** + Jinja-templates | Upload-formulieren, resultaatpagina's, downloads. |
| Database | **Supabase Postgres** (EU) | Alembic-migraties. Back-ups en beheer via Supabase. |
| Login | **Supabase Auth** — code per e-mail | Geen wachtwoordbeheer; alleen `@kwekerijbaas.nl`. |
| Domein | `uf.kwekerijbaas.nl` via **CNAME** + managed TLS | Zie §4. |
| CI | **GitHub Actions** | Draait migraties en tests bij elke push. |

## 3. Toegang

Inloggen gaat met een **code per e-mail** (Supabase Auth): adres invullen, code
uit de mail overtypen, klaar. Geen wachtwoorden om te beheren of te lekken.

- Alleen adressen binnen `TOEGESTANE_DOMEINEN` (standaard `kwekerijbaas.nl`)
  krijgen toegang; losse uitzonderingen via `TOEGESTANE_EMAILS`.
- De controle staat op drie plekken: vóór het versturen van de code, ná
  verificatie, en bij élk verzoek op basis van het sessiecookie. Wie uit het
  domein valt, is met een bestaand cookie meteen buiten.
- Een middleware sluit **de hele app** af in plaats van route voor route; alleen
  `/gezondheid` en het inlogscherm zijn open. Een vergeten dependency op één
  route kan de loongegevens dus niet openzetten.
- Sessie 12 uur geldig, cookie ondertekend met `SESSIE_GEHEIM`, `HttpOnly` en
  `Secure`.

**Instellen in Supabase:** Authentication → Providers → Email aanzetten (met
"Confirm email"), en bij Email Templates de *Magic Link*-template zo laten dat
`{{ .Token }}` in de mail staat — de app vraagt om de code, niet om een klik.

## 4. Eigen domein + DNS

1. In Render: **Settings → Custom Domains → Add** `uf.kwekerijbaas.nl`.
2. Render toont de doel-hostname voor het `CNAME`-record.
3. In het DNS-beheer van `kwekerijbaas.nl`:
   `CNAME  uf  →  <naam>.onrender.com`
4. Render regelt het TLS-certificaat automatisch (Let's Encrypt, auto-verlenging).

Tot het domein staat is de app bereikbaar op het standaardadres van Render.

## 4b. Supabase-project

Project **UF urencontrole** (`icfmqxiigxvzumvqiyba`), regio `eu-central-1`
(Frankfurt, dezelfde regio als de app), Postgres 17. Het schema is aangemaakt
uit de Alembic-migraties; `alembic_version` staat op `b7c1d4e9f2a3`, zodat een
latere `alembic upgrade head` vanaf de container verder gaat waar dit ophield.

**Row Level Security staat aan op alle tabellen, bewust zonder policies.**
Zonder RLS is elke tabel via Supabase's REST-API te lezen en te wijzigen met de
anon-sleutel — en die sleutel is bedoeld om in browsers te staan, dus niet
geheim. Met RLS aan en géén policies is die weg volledig dicht. De app raakt dat
niet: die verbindt rechtstreeks met Postgres als eigenaar van de tabellen, en
een eigenaar omzeilt RLS. Supabase wordt hier alleen gebruikt voor de database
en voor Auth, nooit voor data-toegang via de REST-API.

> De database-linter meldt hierover `rls_enabled_no_policy` op INFO-niveau. Dat
> is de gewenste situatie — **voeg geen policies toe** om die melding weg te
> krijgen; dat zou de REST-API juist weer openzetten.

## 4c. E-mailbezorging (Resend)

De ingebouwde mailserver van Supabase is bedoeld om te proberen (enkele mails
per uur) en niet voor dagelijks gebruik. De inlogcodes gaan daarom via Resend.

**Verifieer een subdomein, niet het hoofddomein.** De gewone bedrijfsmail loopt
via Microsoft 365 en hangt aan het SPF-record van `kwekerijbaas.nl`. Door
`mail.kwekerijbaas.nl` te gebruiken blijft dat record ongemoeid: gaat er iets
mis in de mailconfiguratie, dan raakt dat alleen deze app en niet de
bedrijfsmail.

1. **Resend** → Domains → `mail.kwekerijbaas.nl`, regio EU (Ireland).
2. **DNS** → de door Resend getoonde MX-, SPF- en DKIM-records op het
   subdomein zetten. De records van het hoofddomein niet aanpassen.
3. **Resend** → API Keys → sleutel met alleen *Sending access*.
4. **Supabase** → Authentication → Emails → SMTP Settings:

   | Veld | Waarde |
   |---|---|
   | Sender email | `uf@mail.kwekerijbaas.nl` |
   | Sender name | `UF urencontrole` |
   | Host | `smtp.resend.com` |
   | Port | `465` |
   | Username | `resend` (letterlijk) |
   | Password | de Resend API-sleutel |

5. **Supabase** → Authentication → Rate Limits → e-maillimiet ophogen (bv. 30
   per uur); de lage standaard hoort bij de ingebouwde mailserver.

**Mailtemplate** (Authentication → Emails → Magic link or OTP): de app vraagt om
een code, niet om een klik. Gebruik `{{ .Token }}` in de body en laat
`{{ .ConfirmationURL }}` weg — een link zou naar de Site URL van Supabase leiden
in plaats van naar de app, wat op een mislukte login lijkt.

**Bezorging nagaan:** Resend → Logs toont per bericht of het verstuurd,
geweigerd of gebounced is. Dat leest duidelijker dan de Supabase-logs.

## 5. Geheimen & configuratie

- **Geen** secrets in de repo. `render.yaml` markeert `DATABASE_URL`,
  `SUPABASE_URL` en `SUPABASE_ANON_KEY` als `sync: false`: die vul je in de
  Render-omgeving in. `SESSIE_GEHEIM` laat Render zelf genereren.
- `DATABASE_URL` komt uit Supabase → Project Settings → Database → Connection
  string (URI). Neem de **Session pooler**-variant en vervang `postgresql://`
  door `postgresql+psycopg://`.
- `SUPABASE_URL` is `https://icfmqxiigxvzumvqiyba.supabase.co`;
  `SUPABASE_ANON_KEY` haal je uit Project Settings → API.
- Lokale ontwikkeling via een `.env` (in `.gitignore`), met een
  `.env.example` als sjabloon.

## 6. Wekelijkse flow voor de gebruiker

1. Inloggen op `uf.kwekerijbaas.nl` (code per e-mail).
2. Week kiezen; per UZB de **SNOOP (.xlsx)** en **Nitea (.pdf)** uploaden.
3. App genereert de **urenoverzichten** (download per UZB).
4. Optioneel: **UZB-factuur (.pdf)** uploaden → **factuurcontrole** met
   afwijkingen + concept-**bevindingenmail**.
5. Bij een CAO-/minimumloonwijziging: onder **Loontabellen** de nieuwe
   CAO-loontabel met **ingangsdatum** uploaden (zie SPEC §6). De tarieven van
   alle uitzendbureaus bewegen vanaf die datum automatisch mee — verder niets.

## 7. Bouwvolgorde & status

1. **Spec + architectuur** vastgelegd (`docs/`). ✅
2. **Datamodel + CAO-calculatie-engine + tests** (`backend/`). ✅ *(al aanwezig)*
3. **Ingest** — SNOOP (.xlsx) en Nitea (.pdf) inlezen (`services/ingest/`). ✅
4. **Tariefmapping per UZB + bedrag** (SPEC §5, `services/tarief/`). ✅
5. **Tariefkaart in de app** — CAO-loontabel- én tariefkaart-upload met
   ingangsdatum, afgeleide omrekenfactoren en validatie (SPEC §6). ✅
6. **Web-laag** — inloggen, upload, resultaat en downloads. ✅
7. **Factuurcontrole** (SPEC §7) + bevindingenmail-generator. ⏳
8. **Deploy** — Supabase-project aangemaakt, schema gemigreerd, RLS aan. ✅
   *Openstaand: e-mail-login aanzetten in Supabase, Render-service koppelen en
   het `CNAME`-record zetten.* ⏳
