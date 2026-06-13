"""Genereer een tekst-bestand met een mail-concept naar de UZB.

Het is een standaard mail die de arbeidsplanner kan kopiëren naar Outlook.
Bevat alleen afwijkingen die om actie vragen — geen ruis.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path


# Aanhef per UZB
AANHEF = {
    "L1": "Beste Level 1",
    "SW": "Beste Sterk Werk",
    "CK": "Beste CervoKordaat",
}


def bouw_mail(
    resultaten: list,         # MatchResultaat
    week_doorgegeven,
    facturen: list,           # FactuurL1
    samenvatting: dict,
    output_pad: str | Path,
    afzender: str = "<arbeidsplanner Kwekerij Baas>",
) -> Path:
    pad = Path(output_pad)
    pad.parent.mkdir(parents=True, exist_ok=True)

    week = week_doorgegeven.week_nr
    jaar = week_doorgegeven.jaar
    uzb = week_doorgegeven.uzb_code
    aanhef = AANHEF.get(uzb, f"Beste {uzb}")

    factuurnrs = sorted({f.factuurnummer for f in facturen if f.factuurnummer})

    teveel = [r for r in resultaten if r.hoofdstatus == "niet_in_excel"
              or (r.hoofdstatus == "uren_afwijking" and r.uren_verschil > 0)]
    teweinig = [r for r in resultaten if r.hoofdstatus == "uren_afwijking"
                and r.uren_verschil < 0]
    niet_op_factuur = [r for r in resultaten if r.hoofdstatus == "niet_op_factuur"]
    contractvorm_switch = [r for r in resultaten
                           if r.hoofdstatus == "contractvorm_switch"]
    tarief_afwijking = [r for r in resultaten if r.hoofdstatus == "tarief_afwijking"]

    geen_actie_punten = (
        not teveel and not teweinig and not niet_op_factuur
        and not contractvorm_switch and not tarief_afwijking
    )

    regels: list[str] = []
    regels.append(f"Aan:        <contactpersoon {uzb}>")
    regels.append(f"CC:         ")
    regels.append(f"Onderwerp:  Controle factuur week {week}-{jaar} ({', '.join(factuurnrs) or 'factuurnr onbekend'})")
    regels.append("")
    regels.append(aanhef + ",")
    regels.append("")

    if geen_actie_punten:
        regels.append(
            f"Wij hebben de factuur(nrs {', '.join(factuurnrs) or 'n.v.t.'}) voor week {week}/{jaar} "
            "vergeleken met onze doorgegeven uren en geen afwijkingen gevonden."
        )
        regels.append("")
        regels.append("Met vriendelijke groet,")
        regels.append(afzender)
        pad.write_text("\n".join(regels), encoding="utf-8")
        return pad

    regels.append(
        f"Wij hebben de factuur(nrs {', '.join(factuurnrs)}) voor week {week}/{jaar} "
        "vergeleken met onze doorgegeven uren. Hierbij zijn de volgende punten naar voren gekomen:"
    )
    regels.append("")

    # 1. Te veel gefactureerd → credit aanvragen
    if teveel:
        regels.append("1) UREN OF MEDEWERKERS OP DE FACTUUR DIE NIET IN ONZE DOORGEGEVEN UREN STAAN")
        regels.append("   (verzoek tot creditering)")
        regels.append("")
        for r in teveel:
            if r.hoofdstatus == "niet_in_excel":
                bedrag_str = f"€ {r.bedrag_factuur:.2f}".replace(".", ",")
                regels.append(
                    f"   • {r.naam_factuur}: gefactureerd {r.uren_factuur} uur "
                    f"({bedrag_str}). Geen doorgegeven uren bekend."
                )
            else:
                gem_t = (r.bedrag_factuur / r.uren_factuur) if r.uren_factuur else 0
                impact = round(r.uren_verschil * gem_t, 2)
                bedrag_str = f"€ {impact:.2f}".replace(".", ",")
                regels.append(
                    f"   • {r.naam_factuur} (intern: {r.naam_excel}): "
                    f"gefactureerd {r.uren_factuur} uur, doorgegeven {r.uren_excel} uur. "
                    f"Verschil +{r.uren_verschil:.2f} uur ({bedrag_str})."
                )
        regels.append("")
        tot_uren = sum(r.uren_verschil if r.hoofdstatus == "uren_afwijking" else r.uren_factuur
                       for r in teveel)
        tot_eur = samenvatting.get("te_veel_gefactureerd_eur", 0)
        bedrag_str = f"€ {tot_eur:.2f}".replace(".", ",")
        regels.append(f"   Totaal te crediteren: {tot_uren:.2f} uur ({bedrag_str}).")
        regels.append("")

    # 2. Te weinig gefactureerd
    if teweinig:
        regels.append("2) MEDEWERKERS WAARVOOR MINDER UREN ZIJN GEFACTUREERD DAN DOORGEGEVEN")
        regels.append("   (graag aanvullen)")
        regels.append("")
        for r in teweinig:
            regels.append(
                f"   • {r.naam_factuur or r.naam_excel} (nr {r.nr_excel}): "
                f"gefactureerd {r.uren_factuur} uur, doorgegeven {r.uren_excel} uur. "
                f"Verschil {r.uren_verschil:.2f} uur."
            )
        regels.append("")

    # 3. Niet op factuur
    if niet_op_factuur:
        regels.append("3) DOORGEGEVEN MEDEWERKERS DIE NIET OP DEZE FACTUUR STAAN")
        regels.append("   (graag bevestigen of zij op een andere factuur — bv. Payroll — staan)")
        regels.append("")
        for r in niet_op_factuur:
            regels.append(
                f"   • {r.naam_excel} (nr {r.nr_excel}): "
                f"{r.uren_excel} uur doorgegeven."
            )
        regels.append("")

    # 4. Contractvorm-switch
    if contractvorm_switch:
        regels.append("4) MEDEWERKERS MET TARIEF/CONTRACTVORM-WIJZIGING BINNEN ÉÉN WEEK")
        regels.append("   (graag bevestigen dat dit klopt)")
        regels.append("")
        for r in contractvorm_switch:
            schalen = sorted({(t.loonschaal, t.contractvorm)
                              for t in r.tarief_regels if t.loonschaal})
            beschrijving = " → ".join(f"{ls} {cv}" for ls, cv in schalen)
            regels.append(
                f"   • {r.naam_factuur or r.naam_excel}: {beschrijving}"
            )
        regels.append("")

    # 5. Tarief-afwijking
    if tarief_afwijking:
        regels.append("5) TARIEVEN OP FACTUUR DIE NIET IN ONZE TARIEVENTABEL VOORKOMEN")
        regels.append("   (graag bevestigen of de tarieventabel een update nodig heeft)")
        regels.append("")
        for r in tarief_afwijking:
            for t in r.tarief_regels:
                if not t.binnen_tolerantie:
                    regels.append(
                        f"   • {r.naam_factuur}: tarief €{t.tarief_factuur:.4f} "
                        f"@ categorie {t.categorie} — onbekend in onze tabel."
                    )
        regels.append("")

    regels.append(
        "Wij ontvangen graag jullie reactie. Een gedetailleerd overzicht "
        "van de controle is bijgevoegd als Excel."
    )
    regels.append("")
    regels.append("Met vriendelijke groet,")
    regels.append(afzender)
    regels.append("")
    regels.append(
        f"--\nDit concept is gegenereerd op {datetime.now().strftime('%Y-%m-%d %H:%M')} "
        f"door uren_controle. Controleer en pas aan voor verzending."
    )

    pad.write_text("\n".join(regels), encoding="utf-8")
    return pad
