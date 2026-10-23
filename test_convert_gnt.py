"""SBLGNT parsing in scripts/convert_gnt.py (no network)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))

from convert_gnt import parse_sblgnt


def test_parse_sblgnt_skips_title_and_strips_apparatus_sigla():
    text = (
        "﻿ΚΑΤΑ ΜΑΘΘΑΙΟΝ\n"
        "Matt 1:6\tΔαυὶδ ⸀δὲ ἐγέννησεν ⸂τὸν Σολομῶνα⸃ ἐκ τῆς τοῦ Οὐρίου, \n"
        "Matt 17:21\t \n"
    )
    assert parse_sblgnt(text) == [
        ("Matt 1:6", "Δαυὶδ δὲ ἐγέννησεν τὸν Σολομῶνα ἐκ τῆς τοῦ Οὐρίου,"),
    ]
