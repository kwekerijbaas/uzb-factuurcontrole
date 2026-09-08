# UF — UZB-urencontrole

Webapp voor de wekelijkse controle van uitzendbureau-uren en -facturen bij
Kwekerij Baas. Ola of Jacob laadt per week de SNOOP-export (Excel) en het
Nitea-overzicht (PDF) in; de app berekent per uitzendkracht de uren per
CAO-toeslagcategorie en het inkoopbedrag, en legt later de facturen van de
uitzendbureaus ernaast.

- **App:** https://uzb-factuurcontrole.onrender.com (inloggen met een code
  per e-mail, alleen @kwekerijbaas.nl)
- **Rekenspecificatie:** [docs/SPEC.md](docs/SPEC.md)
- **Architectuur & beheer:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- **Code:** `backend/` (FastAPI + calc-engine + templates), deploy via
  `render.yaml` (Render, Docker) op een Supabase-database.

Deze repo is de voortzetting van de app die eerder in
`kwekerijbaas/artikelinvoer` werd ontwikkeld; de volledige historie is mee
overgeheveld.
