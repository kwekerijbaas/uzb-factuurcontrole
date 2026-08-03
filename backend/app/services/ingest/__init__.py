"""Inlezen van de weekbronnen naar de calculatie-domeintypes.

- SNOOP (.xlsx) -> planning (PlanningRegel) + loonschaal per medewerker.
- Nitea (.pdf)  -> registratie (RegistratieRegel) per medewerker.

De parsers zijn puur (pad of bytes in, dataclasses uit) zodat ze los te testen
zijn, net als de calc-engine.
"""

from .loontabel import lees_loontabel
from .nitea import NiteaMedewerker, lees_nitea
from .snoop import SnoopMedewerker, lees_snoop

__all__ = [
    "lees_snoop",
    "SnoopMedewerker",
    "lees_nitea",
    "NiteaMedewerker",
    "lees_loontabel",
]
