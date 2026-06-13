"""Streamlit controlepaneel voor de UZB-urencontrole.

Eén knop: detecteert welke facturen / SNOOP / Nitea-bestanden NIEUW of GEWIJZIGD
zijn in de centrale controle-map sinds de vorige run, en draait met één klik de
volledige controle (productie-engine `controle_uzb.py`).

Start (zie .claude/launch.json):
    python -m streamlit run uren_controle_dev/uren_controle/app.py
Daarna: http://localhost:8501
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st

# ── Paden ───────────────────────────────────────────────────────────────────
# De productie-engine (controle_uzb.py) staat in de projectmap; die map is ook
# de werkdirectory waar `snoop`, `bureau_profielen` en `toeslag` te vinden zijn.
ENGINE_DIR = Path(__file__).resolve().parents[2]          # ...\Directie - Projecten
ENGINE = ENGINE_DIR / "controle_uzb.py"

# Profielen van de engine herbruiken voor bureau-/week-herkenning.
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))
try:
    import bureau_profielen as bp
except Exception:
    bp = None

BUREAU_NAAM = {
    "L1": "Level One Uitzendbureau",
    "LP": "Level One Payroll",
    "SW": "Sterk Werk",
    "CK": "CervoKordaat / Workstead",
}
DEFAULT_MAP = r"C:\Users\dieter.KWEKERIJBAAS\Kwekerij Baas\Finance - Controle 2026"
STATE_NAAM = ".controle_state.json"

# Bestanden die de engine zelf genereert -> niet meetellen als "input".
OUTPUT_PREFIXES = (
    "rapportage_urencontrole", "mail_concept_", "mail_intern_", "mail_extern_",
    "credits_overzicht", "overzicht_afwijkingen_", "openstaande_posten_register",
)
INPUT_EXTS = (".pdf", ".xls", ".xlsx")


# ── Helpers ───────────────────────────────────────────────────────────────────
def is_input_bestand(naam: str) -> bool:
    low = naam.lower()
    if low.startswith("~$") or low.startswith("."):
        return False
    if not low.endswith(INPUT_EXTS):
        return False
    if any(low.startswith(p) for p in OUTPUT_PREFIXES):
        return False
    return True


def soort(naam: str) -> str:
    low = naam.lower()
    if "snoop" in low:
        return "📋 SNOOP"
    if "itea" in low and low.endswith(".pdf"):
        return "🕑 Nitea"
    if low.endswith(".pdf"):
        return "📄 Factuur"
    return "📊 Doorgegeven uren"


def _hint_match(naam_low: str, hint) -> bool:
    hints = [hint] if isinstance(hint, str) else list(hint)
    for h in hints:
        if h and re.search(r"(?<![a-z])" + re.escape(h.lower()) + r"(?![a-z])", naam_low):
            return True
    return False


def detecteer_bureau(naam: str) -> str | None:
    """Bureau-code (L1/LP/SW/CK) o.b.v. bestandsnaam — payroll vóór uitzendbureau."""
    low = naam.lower()
    if "level one payroll" in low:
        return "LP"
    if "level one uitzendbureau" in low:
        return "L1"
    if "sterk werk" in low:
        return "SW"
    if any(k in low for k in ("cervokordaat", "kordaat", "workstead")):
        return "CK"
    # doorgegeven-uren / Nitea: via de doorgegeven_hint van elk profiel
    if bp is not None:
        for prof in bp.BUREAUS:
            if _hint_match(low, prof.doorgegeven_hint):
                return prof.code
    return None


def detecteer_week_naam(naam: str) -> int | None:
    """Weeknummer uit de bestandsnaam (WK 15 / wk15 / week 15)."""
    m = re.search(r"\bw(?:ee)?k\s*0*(\d{1,2})\b", naam.lower())
    return int(m.group(1)) if m else None


@st.cache_data(show_spinner=False)
def weken_uit_factuur(pad_str: str, mtime: float, code: str) -> list[int]:
    """Weeknummers uit een factuur-PDF (gecached op pad+mtime; alleen voor PDF's)."""
    if bp is None:
        return []
    try:
        import pdfplumber
        with pdfplumber.open(pad_str) as pdf:
            text = "\n".join((p.extract_text() or "") for p in pdf.pages)
        prof = next((b for b in bp.BUREAUS if b.code == code), None)
        if prof is None:
            return []
        regels = prof.parse_factuur(text, "x")
        weken = {int(r.week) for r in regels if str(getattr(r, "week", "")).isdigit()}
        return sorted(weken)
    except Exception:
        return []


def analyseer(map_pad: Path, namen: list[str]) -> dict:
    """Groepeert bestandsnamen naar {code: {week: {soort: aantal}}} + 'onbekend'."""
    groepen: dict = {}
    for naam in namen:
        low = naam.lower()
        if "snoop" in low:
            groepen.setdefault("SNOOP", {}).setdefault("—", {})
            groepen["SNOOP"]["—"]["SNOOP-lijst"] = groepen["SNOOP"]["—"].get("SNOOP-lijst", 0) + 1
            continue
        code = detecteer_bureau(naam)
        s = soort(naam)
        # week bepalen: uit naam; voor facturen anders uit de PDF zelf
        weken: list = []
        wk = detecteer_week_naam(naam)
        if wk is not None:
            weken = [wk]
        elif low.endswith(".pdf") and "itea" not in low and code:
            p = map_pad / naam
            try:
                weken = weken_uit_factuur(str(p), round(p.stat().st_mtime, 2), code) or []
            except Exception:
                weken = []
        sleutel = code or "onbekend"
        wk_lijst = weken if weken else ["?"]
        for w in wk_lijst:
            groepen.setdefault(sleutel, {}).setdefault(w, {})
            groepen[sleutel][w][s] = groepen[sleutel][w].get(s, 0) + 1
    return groepen


def scan_inputs(map_pad: Path) -> dict[str, float]:
    """{bestandsnaam: mtime} voor alle relevante input-bestanden in de map."""
    out: dict[str, float] = {}
    if not map_pad.is_dir():
        return out
    for p in map_pad.iterdir():
        if p.is_file() and is_input_bestand(p.name):
            out[p.name] = round(p.stat().st_mtime, 2)
    return out


def laad_state(map_pad: Path) -> dict:
    f = map_pad / STATE_NAAM
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def schrijf_state(map_pad: Path, files: dict[str, float]) -> None:
    data = {"laatste_run": datetime.now().isoformat(timespec="seconds"), "bestanden": files}
    (map_pad / STATE_NAAM).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def bepaal_nieuw(huidig: dict[str, float], state: dict) -> tuple[list[str], list[str]]:
    """Geeft (nieuwe_bestanden, gewijzigde_bestanden) t.o.v. de opgeslagen state."""
    oud = state.get("bestanden", {}) if isinstance(state, dict) else {}
    nieuw, gewijzigd = [], []
    for naam, mtime in huidig.items():
        if naam not in oud:
            nieuw.append(naam)
        elif abs(oud[naam] - mtime) > 0.5:
            gewijzigd.append(naam)
    return sorted(nieuw), sorted(gewijzigd)


def run_engine(map_pad: Path) -> tuple[int, str]:
    """Draait controle_uzb.py op de map; geeft (exitcode, gecombineerde output)."""
    cmd = [sys.executable, str(ENGINE), "--map", str(map_pad)]
    proc = subprocess.run(
        cmd, cwd=str(ENGINE_DIR), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def lijst_outputs(map_pad: Path) -> dict[str, list[Path]]:
    """Verzamelt de gegenereerde bestanden, gegroepeerd voor weergave."""
    groepen: dict[str, list[Path]] = {"Rapportages": [], "Credits": [], "Overzichten / mails": [], "Register": []}
    if not map_pad.is_dir():
        return groepen
    for p in sorted(map_pad.iterdir()):
        if not p.is_file():
            continue
        low = p.name.lower()
        if low.startswith("rapportage_urencontrole"):
            groepen["Rapportages"].append(p)
        elif low.startswith("credits_overzicht"):
            groepen["Credits"].append(p)
        elif low.startswith(("overzicht_afwijkingen_", "mail_")):
            groepen["Overzichten / mails"].append(p)
        elif low.startswith("openstaande_posten_register"):
            groepen["Register"].append(p)
    return groepen


# ── Pagina ──────────────────────────────────────────────────────────────────
st.set_page_config(page_title="UZB Urencontrole", page_icon="📊", layout="wide")
st.title("📊 UZB Urencontrole — controlepaneel")
st.caption("Zet je facturen / SNOOP / Nitea in de map en klik op de knop. "
           "De controle ontdekt zelf welke weken en bureaus erin zitten.")

with st.sidebar:
    st.header("Map")
    map_str = st.text_input("Controle-map", value=DEFAULT_MAP)
    map_pad = Path(map_str)
    if map_pad.is_dir():
        st.success("✓ Map gevonden")
    else:
        st.error("✗ Map bestaat niet")
    if not ENGINE.exists():
        st.error(f"✗ Engine niet gevonden: {ENGINE}")
    state = laad_state(map_pad)
    if state.get("laatste_run"):
        st.caption(f"Laatste run: {state['laatste_run']}")

# ── Detectie van nieuwe / gewijzigde bestanden ───────────────────────────────
huidig = scan_inputs(map_pad)
nieuw, gewijzigd = bepaal_nieuw(huidig, state)

st.subheader("Wat staat er in de map?")
c1, c2, c3 = st.columns(3)
c1.metric("Input-bestanden totaal", len(huidig))
c2.metric("🆕 Nieuw sinds vorige run", len(nieuw))
c3.metric("✏️ Gewijzigd", len(gewijzigd))

if nieuw or gewijzigd:
    te_tonen = nieuw + gewijzigd
    with st.spinner("Bureaus en weken herkennen…"):
        groepen = analyseer(map_pad, te_tonen)
    st.markdown("**Samenvatting per bureau / week:**")
    if "SNOOP" in groepen:
        st.markdown("- 📋 **SNOOP-lijst** bijgewerkt (inschaling/tarief — alle bureaus)")
    # vaste volgorde van bureaus, onbekend onderaan
    bekend = ("L1", "LP", "SW", "CK", "onbekend", "SNOOP")
    volgorde = ["L1", "LP", "SW", "CK"] + [k for k in groepen if k not in bekend]
    for code in volgorde + (["onbekend"] if "onbekend" in groepen else []):
        if code not in groepen:
            continue
        naam_b = BUREAU_NAAM.get(code, "Bureau onbekend") if code != "onbekend" else "⚠️ Bureau niet herkend"
        weken = groepen[code]
        # weken sorteren (numeriek, '?' achteraan)
        def _wk_key(w):
            return (1, 0) if w == "?" else (0, int(w))
        delen = []
        for w in sorted(weken, key=_wk_key):
            soorten = weken[w]
            stukjes = ", ".join(
                f"{n}× {lbl.split(' ', 1)[-1]}" if n > 1 else lbl.split(" ", 1)[-1]
                for lbl, n in sorted(soorten.items())
            )
            wk_label = "wk ?" if w == "?" else f"wk{int(w):02d}"
            delen.append(f"**{wk_label}**: {stukjes}")
        st.markdown(f"- 🏢 **{code}** ({naam_b}) — " + " · ".join(delen))
    with st.expander(f"Toon alle {len(te_tonen)} nieuwe/gewijzigde bestanden", expanded=False):
        for naam in nieuw:
            st.markdown(f"- 🆕 {soort(naam)} — `{naam}`")
        for naam in gewijzigd:
            st.markdown(f"- ✏️ {soort(naam)} — `{naam}` (gewijzigd)")
elif huidig:
    st.info("Geen nieuwe of gewijzigde bestanden sinds de vorige run. "
            "Je kunt alsnog opnieuw draaien (de controle is idempotent).")
else:
    st.warning("Geen input-bestanden in de map gevonden.")

# ── Knop: controle draaien ────────────────────────────────────────────────────
st.divider()
knop_label = "🔍 Controleer nieuwe bestanden" if (nieuw or gewijzigd) else "🔄 Controle opnieuw draaien"
if st.button(knop_label, type="primary", use_container_width=True, disabled=not map_pad.is_dir()):
    with st.spinner("Bezig met controleren — facturen, schaal/tarief, toeslag en credits…"):
        code, output = run_engine(map_pad)
        schrijf_state(map_pad, huidig)
    if code == 0:
        st.success("✅ Controle afgerond")
    else:
        st.error(f"⚠️ Engine eindigde met code {code} — zie log hieronder.")
    with st.expander("Log van de run", expanded=(code != 0)):
        st.code(output or "(geen output)", language=None)
    st.session_state["laatste_output"] = output
    # rerun zodat de outputs-sectie ververst
    st.rerun()

# ── Gegenereerde resultaten ───────────────────────────────────────────────────
st.divider()
st.subheader("Resultaten in de map")
st.caption(f"📁 {map_pad}")
groepen = lijst_outputs(map_pad)
totaal_out = sum(len(v) for v in groepen.values())
if totaal_out == 0:
    st.info("Nog geen resultaten — draai eerst de controle.")
else:
    for titel, paden in groepen.items():
        if not paden:
            continue
        st.markdown(f"**{titel}** ({len(paden)})")
        for p in paden:
            cols = st.columns([5, 1])
            cols[0].text(p.name)
            try:
                with open(p, "rb") as fh:
                    cols[1].download_button("⬇️", data=fh.read(), file_name=p.name,
                                            key=f"dl_{p.name}", use_container_width=True)
            except Exception:
                cols[1].caption("—")
