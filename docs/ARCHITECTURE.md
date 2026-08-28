# Architectuur & deploy — UZB-urencontrole

De UZB-urencontrole wordt een beveiligde webapp waar Ola en Jacob wekelijks de
SNOOP- (Excel) en Nitea- (PDF) bestanden inladen en de urenoverzichten +
factuurcontrole terugkrijgen.

## 1. Opzet in het kort

```
Browser (Ola/Jacob)
   │  HTTPS, ingelogd met een code per e-mail
   ▼
baaskwekerij.nl/uf  ──doorverwijzing──►  Render (Docker, Frankfurt)
   │                              backend/  FastAPI + calc-engine + templates
   │
   ├── Supabase Auth       inlogcode per e-mail, alleen @kwekerijbaas.nl
   └── Supabase Postgres   loontabellen, omrekenfactoren (SCD2), weekcontroles
```

**Waarom een aparte plek voor de app:** de app moet bij elk gebruik bestanden
ontvangen en uitlezen, rekenen, de database raadplegen en een Excel-bestand
teruggeven. Dat is serverwerk. GitHub Pages (waar `baaskwekerij.nl` op draait)
serveert alleen stilstaande bestanden en kan dat dus niet; Supabase evenmin,
want Edge Functions zijn Deno/TypeScript terwijl de rekenkern Python is
(`pdfplumber` voor de Nitea-PDF's). Vandaar een container-hoster.

**Kosten:** de gratis laag van Render volstaat. Die valt na 15 minuten zonder
verkeer stil, waardoor het eerste bezoek zo'n minuut opstarttijd heeft — voor
een wekelijkse controle door twee mensen is dat prima. Stoort dat wachten, dan
is `plan: starter` in `render.yaml` (~EUR 7/mnd) genoeg. Google Cloud Run is een
alternatief dat bij dit gebruiksvolume ook gratis blijft en dezelfde image
draait.

**Repo-structuur (`kwekerijbaas/uzb-factuurcontrole`, branch
`claude/staffing-hours-app-z19SH`):** `backend/` (datamodel, migraties,
calc-engine, web-laag, tests), `docs/` (deze docs + CAO-bron), `render.yaml`
(deploy). De app is in augustus 2026 met volledige historie overgeheveld uit
`kwekerijbaas/artikelinvoer`.

> **Versiebeheer van tarieven** is al in het datamodel verankerd via een
> SCD2-patroon (`geldig_van`/`geldig_tot` op `loonschaal` en `toeslag_regel`) —
> dit dekt de CAO-/minimumloonwijzigingen uit SPEC §6.

## 2. Componenten

| Component | Keuze | Toelichting |
|---|---|---|
| Runtime | **Render** (Docker, Frankfurt, gratis laag) | Bouwt `backend/Dockerfile` uit deze repo. Fly.io, Railway of Cloud Run werken met dezelfde image. |
| Web framework | **FastAPI** + Jinja-templates | Upload-formulieren, resultaatpagina's, downloads. |
| Database | **Supabase Postgres** (EU) | Alembic-migraties. Back-ups en beheer via Supabase. |
| Login | **Supabase Auth** — code per e-mail | Geen wachtwoordbeheer; alleen `@kwekerijbaas.nl`. |
| Adres | `<naam>.onrender.com`, ingang via `baaskwekerij.nl/uf` | Zie §4. |
| CI | **GitHub Actions** | Draait migraties en tests bij elke push. |

## 3. Toegang

Inloggen gaat met een **code per e-mail** (Supabase Auth): adres invullen, code
uit de mail overtypen, klaar. Geen wachtwoorden om te beheren of te lekken.

- Alleen adressen binnen `TOEGESTANE_DOMEINEN` (standaard `kwekerijbaas.nl`)
  krijgen toegang; losse uitzonderingen via `TOEGESTANE_EMAILS`.
- **Offboarding:** zet het adres van een vertrokken medewerker in
  `GEBLOKKEERDE_EMAILS` (bv. `'["tim@kwekerijbaas.nl"]'`) in de
  Render-omgeving. Dat blokkeert direct — ook een nog lopend sessiecookie —
  want elk verzoek loopt langs die controle. Verwijder daarnaast de gebruiker
  in Supabase (Authentication → Users) voor de volledigheid.
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

## 4. Adres van de app

De app draait op **`https://uzb-factuurcontrole.onrender.com`** (Render-service
`uzb-factuurcontrole`, gekoppeld aan deze repo; de oude service
`uf-urencontrole` uit de artikelinvoer-repo is per 27-08-2026 gesuspend).
**`uf.kwekerijbaas.nl` is niet beschikbaar: dat
subdomein is in gebruik door een andere app** (het DNS-record `uf` →
`...azurestaticapps.net` hoort daarbij en moet blijven staan). Het
Render-adres staat al in de instructiemails aan het team. Optioneel kan
`baaskwekerij.nl/uf` als doorverwijzing dienen (zie hieronder); nodig is dat
niet.

Mocht een eigen subdomein later alsnog gewenst zijn, kies dan een vrije naam
(bv. `uren.kwekerijbaas.nl`): een `CNAME`-record naar
`uzb-factuurcontrole.onrender.com.` plus **Custom Domains** in Render volstaat —
verder verandert er niets.

**Twee domeinen, let op het verschil:** `kwekerijbaas.nl` (e-mail, en
`send.kwekerijbaas.nl` voor de inlogmail) en `baaskwekerij.nl` (website). De
namen lijken op elkaar maar zijn omgedraaid.

### Doorverwijzing vanaf baaskwekerij.nl/uf

`baaskwekerij.nl` draait op **GitHub Pages** (A-records naar 185.199.108-111.153,
`www` als CNAME naar `kwekerijbaas.github.io`). Dat serveert alleen statische
bestanden, dus een reverse proxy die `/uf` naar de app doorzet is niet mogelijk
— een doorverwijzing wel.

Voeg in de Pages-repo het bestand `uf/index.html` toe en vul het Render-adres in:

```html
<!doctype html>
<html lang="nl">
<head>
  <meta charset="utf-8">
  <title>UF urencontrole</title>
  <meta http-equiv="refresh" content="0; url=https://UF-ADRES.onrender.com/">
  <script>location.replace("https://UF-ADRES.onrender.com/");</script>
</head>
<body>
  <p>Je wordt doorgestuurd naar de urencontrole.
     Gebeurt er niets? <a href="https://UF-ADRES.onrender.com/">Klik hier</a>.</p>
</body>
</html>
```

Verhuist de app later naar een eigen subdomein, dan is dit het enige bestand dat
mee moet veranderen.

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

**Status: geconfigureerd en getest op 18-08-2026** — inlogcodes komen van
`uf@send.kwekerijbaas.nl` via Resend (API-sleutel `uf-urencontrole`, alleen
Sending access). De stappen hieronder blijven staan als draaiboek voor het
geval de koppeling ooit opnieuw gezet moet worden.

De ingebouwde mailserver van Supabase is bedoeld om te proberen (enkele mails
per uur) en niet voor dagelijks gebruik. De inlogcodes gaan daarom via Resend.

**Verifieer een subdomein, niet het hoofddomein.** De gewone bedrijfsmail loopt
via Microsoft 365 en hangt aan het SPF-record van `kwekerijbaas.nl`. Door een
apart subdomein te gebruiken blijft dat record ongemoeid: gaat er iets mis in de
mailconfiguratie, dan raakt dat alleen deze app en niet de bedrijfsmail. Welk
subdomein maakt technisch niet uit, zolang Resend, de DNS-records en het
afzenderadres in Supabase dezelfde naam aanhouden.

1. **Resend** → Domains → `send.kwekerijbaas.nl`, regio EU (Ireland).
2. **DNS** → de door Resend getoonde MX-, SPF- en DKIM-records op het
   subdomein zetten. De records van het hoofddomein niet aanpassen.
3. **Resend** → API Keys → sleutel met alleen *Sending access*.
4. **Supabase** → Authentication → Emails → SMTP Settings:

   | Veld | Waarde |
   |---|---|
   | Sender email | `uf@send.kwekerijbaas.nl` |
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

1. Naar `baaskwekerij.nl/uf` en inloggen met een code per e-mail.
2. Week kiezen; per UZB de **SNOOP (.xlsx)** en **Nitea (.pdf)** uploaden.
3. App genereert de **urenoverzichten** (download per UZB). Mist iemand een
   loonschaal, dan wordt de week **niet** verwerkt: de melding noemt wie het
   betreft. Vul de schaal in onder **Uitzendkrachten** (per persoon een
   invulveld) en verwerk de week opnieuw. De namen uit die mislukte poging
   staan er dan al, want die worden vóór de controle vastgelegd.
4. Optioneel: **UZB-factuur (.pdf)** uploaden → **factuurcontrole** met
   afwijkingen + concept-**bevindingenmail**.
5. Bij een CAO-/minimumloonwijziging: onder **Loontabellen** de nieuwe
   CAO-loontabel met **ingangsdatum** uploaden (zie SPEC §6). De tarieven van
   alle uitzendbureaus bewegen vanaf die datum automatisch mee — verder niets.

## 6b. Foutmeldingen nakijken

Uploads worden op bestandstype gecontroleerd vóórdat ze worden ingelezen: de
extensie zegt niets (die is te hernoemen), de eerste bytes wel. Een verkeerd
gekozen bestand — de SNOOP-export in het Nitea-veld, een oud `.xls`, een
factuur in plaats van een urenoverzicht — geeft daardoor een **400 met een
leesbare melding** in plaats van een kale `Internal Server Error`.

Gaat er tóch iets onverwachts mis, dan toont de app een pagina met een
**kenmerk** van acht tekens. Datzelfde kenmerk staat met de volledige traceback
in het log van de hoster (Render → service → *Logs*, zoek op het kenmerk).
Vraag gebruikers dat kenmerk door te geven; zonder is een melding "het werkt
niet" niet terug te vinden.

## 7. Bouwvolgorde & status

1. **Spec + architectuur** vastgelegd (`docs/`). ✅
2. **Datamodel + CAO-calculatie-engine + tests** (`backend/`). ✅ *(al aanwezig)*
3. **Ingest** — SNOOP (.xlsx) en Nitea (.pdf) inlezen (`services/ingest/`). ✅
4. **Tariefmapping per UZB + bedrag** (SPEC §5, `services/tarief/`). ✅
5. **Tariefkaart in de app** — CAO-loontabel- én tariefkaart-upload met
   ingangsdatum, afgeleide omrekenfactoren en validatie (SPEC §6). ✅
6. **Web-laag** — inloggen, upload, resultaat en downloads. ✅
7. **Factuurcontrole** (SPEC §7) + bevindingenmail-generator. ✅
8. **Deploy** — Supabase-project aangemaakt, schema gemigreerd, RLS aan. ✅
   *Openstaand: e-mail-login aanzetten in Supabase, Render-service koppelen en
   de doorverwijspagina plaatsen. Geen DNS-werk nodig.* ⏳
