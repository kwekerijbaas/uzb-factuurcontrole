# Kwekerijbaas — Apps

Drie web-apps in deze repo. Beide eerste zijn pure HTML/CSS/JS en kunnen direct in de browser worden geopend. De derde (`opzet_afname.html`) draait in productie op Azure Static Web Apps achter Entra ID-login.

## 1. Artikelinvoer (`index.html`) — Masterdata

Masterdata beheertool voor Blooming Joy verkoopartikelen.

### Functionaliteiten

- Invoerformulier met live preview van Naam uitgeschreven, Stickernaam en Artikelnr
- 347 bestaande artikelen voorgeladen (Family, Friends, Solo)
- Dropdowns gevuld vanuit Agriware stamdata (geslachten, kleuren, potcodes, eenheden, etc.)
- Nieuwe referentiewaarden toevoegen via + knop (thema, soort, typekleur, potmaat)
- **Inline cell-editing**: klik een cel om direct te bewerken
- **Teelt + Teeltomschrijving** met lookup-lijsten uit Opzet afname 2026 (vrije tekst toegestaan)
- Artikelen bewerken en verwijderen
- Sorteerbare kolommen en filters (zoekbalk + combinatietype)
- CSV-export voor Agriware-import
- JSON backup/import voor persistentie

## 2. Opzet/afname (`opzet_afname.html`) — Verkoop / planning workflow

Hoofd-app voor verkopers en planners om offertes, voorlopige orders, definitieve orders en opzetregels te beheren. Gebaseerd op tabblad **2026** uit *Opzet afname 2026.xlsx*. Op den duur uit te bouwen tot volledig CRM (offertes versturen, klantbeheer, deal-pipeline).

### Functionaliteiten

- **Status flow**: Aanvraag (V) → In behandeling (O) → Voorlopig (B) → Definitief (A/AD/AI) → Vervallen (X)
- Voorgeladen lookup-data uit het Excel: 65 klanten, 68 eindklanten, 28 afzetlanden, 344 VRCs, 168 planningsgroepen, 149 teelten, 386 teeltspecificaties, 26 potmaten, 12 kartypes
- 134 traycodes (uit STAM.fust) bundled als default-lookup voor Tray-veld
- **Agriware-lijsten import** (centraal modaal): vervang klanten / eindklanten / trays / masterdata-artikelen met verse exports uit Agriware (CSV/JSON/XLSX). LocalStorage-persistentie.
- **Verpakking algemeen** sectie: type verpakking, kleur, EAN, plantenpaspoort, verkoopprijs consument, sealen, mix-type, min. contract %, ladingdrager
- **11 bedrukkings-opties** als uitklapbare kaarten: karposter, sticker (pot/hoes), karsticker, etiket, hoes, doos, tray (met traycode-lookup), inlegtray, TGW, draagbeugel, overpakken — elk met eigen 'wie regelt layout / productie / betaald' (Baas / Klant / Derde)
- Ruime invoer-drawer met geplande aantallen, prijs, fust, kar (Ma–Zo verdeling wordt elders bijgehouden voor PowerBI-koppeling)
- **Kopiëren vanuit bestaande regel** (modal-zoeker) → snel varianten toevoegen
- **Bulk-acties**: selecteer meerdere regels en switch status, dupliceer, verwijder, of download Rapid Start XML in batch
- **Inline cell-editing** op week, prijs en geplande aantallen
- **Dashboard-tab** met 4 KPI's (orderregels, geplande aantallen, geplande omzet, unieke klanten) en 3 grafieken (aantallen per week, top 10 klanten op omzet, verdeling per productgroep)
- **Sorteerbare kolommen** met visuele indicator (▲/▼) en lichte ↕-hint op klikbare headers
- **Per-regel Rapid Start XML download** voor import in Business Central (schema benadering — werk samen met BC-team uit voor productie)
- Filteren op status, klant, productgroep, verkoper, jaar, week-range, vrije zoektekst
- VRC-groepering, jaar-overzetten, auto-rollover, klantvragen-import
- Excel-, CSV- en JSON-export
- Image-preview in master-picker

## 3. Legacy: `artikelinvoer.html`

Voorloper van `index.html`. Wordt door SWA serveerd onder `/artikelinvoer.html`.

## Lokaal gebruik

Open `index.html` of `opzet_afname.html` in een browser. Geen server of installatie nodig.

## Hosting in Azure (productie)

`opzet_afname.html` draait op **Azure Static Web Apps** (Standard tier, ~€9/maand) met Microsoft Entra ID-authenticatie (alleen `@kwekerijbaas.nl`-accounts).

### Azure-resources

- **Resource Group**: `rg-opzet-afname-754-msw-prd` (West Europe)
- **Static Web App**: `swa-opzet-afname-754-msw-prd`
- **Default URL**: `https://yellow-meadow-04b0a3c80.7.azurestaticapps.net`
- **Custom domain (na DNS)**: `https://opzetafname.kwekerijbaas.nl`
- **Entra App Registration**: `opzet-afname-app` (single-tenant)

### Deploy-flow

Push naar `main` → workflow `.github/workflows/azure-static-web-apps.yml` deployt automatisch via deployment token in GitHub Secrets (`AZURE_STATIC_WEB_APPS_API_TOKEN`). PR's krijgen automatisch een preview-environment URL.

### Routing

`staticwebapp.config.json` zorgt voor:
- `/` → rewrite naar `/opzet_afname.html`
- Alle routes vereisen `authenticated` rol (Entra ID-login)
- 401 → redirect naar `/.auth/login/aad`
- Andere identity providers (GitHub, Twitter) uitgezet

### Custom domain instellen (eenmalig, zodra DNS klaar is)

1. CNAME zetten bij DNS-provider:
   ```
   Type:    CNAME
   Naam:    opzetafname
   Waarde:  yellow-meadow-04b0a3c80.7.azurestaticapps.net
   TTL:     3600
   ```
2. In Azure:
   ```bash
   az staticwebapp hostname set \
     --name swa-opzet-afname-754-msw-prd \
     --resource-group rg-opzet-afname-754-msw-prd \
     --hostname opzetafname.kwekerijbaas.nl
   ```
3. Redirect-URI uitbreiden in Entra App Registration:
   ```bash
   az ad app update --id <ENTRA_CLIENT_ID> \
     --web-redirect-uris \
     "https://yellow-meadow-04b0a3c80.7.azurestaticapps.net/.auth/login/aad/callback" \
     "https://opzetafname.kwekerijbaas.nl/.auth/login/aad/callback"
   ```

### Admin-consent op Entra App Registration

Bij eerste gebruik (of na het toevoegen van extra API-permissies) moet een Azure-tenant-admin éénmalig admin-consent geven. Zonder deze stap krijgen niet-admin gebruikers een "Need admin approval"-prompt en lukt de login niet.

**Vereiste rol**: Global Administrator, Cloud Application Administrator, of Application Administrator.

1. Azure Portal → **Entra ID** → **App registrations** → zoek `opzet-afname-app` (of de Application (client) ID die als `AAD_CLIENT_ID` in de SWA-configuratie staat).
2. Open de app → **API permissions** in het linkermenu.
3. Klik bovenaan op **"Grant admin consent for [tenantnaam]"** en bevestig.
4. Onder *Status* moeten alle permissies (minimaal `User.Read`) een groen vinkje krijgen.

**Optioneel — toegang beperken tot specifieke gebruikers/groepen**:

1. Entra ID → **Enterprise applications** → dezelfde app openen.
2. *Properties* → **Assignment required? = Yes**.
3. *Users and groups* → voeg de juiste personen of een groep toe.

**Verifiëren**: open een incognito-tab op `https://opzetafname.kwekerijbaas.nl`, log in als niet-admin testgebruiker — de consent-prompt moet weg zijn en de gebruiker landt direct op `opzet_afname.html`.

### Toekomstige uitbreidingen (architectuur)

De gekozen SWA Standard tier ondersteunt een groei-pad naar CRM:
- **Azure Functions** (Consumption plan, pay-per-execution) voor REST API → multi-user data-sync
- **Azure SQL Serverless** of **Cosmos DB** voor persistente data (regels, klanten, offertes, contacten)
- **Azure Blob Storage** voor offerte-PDF's en attachments
- **Microsoft Graph API** voor email-versturen via bestaande M365

## Kwekerijbaas

Drietorensweg / Enserweg — Blooming Joy productlijnen
