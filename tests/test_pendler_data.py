import pytest
import pandas as pd
from pathlib import Path

# The BA Pendlerverflechtungen file
PENDLER_PATH = Path(__file__).parent.parent / "data" / "germany" / "krpend-k-0-202306-xlsx.xlsx"

STUDY_KREISE = {"09273", "09375", "09176", "09362", "09373", "09186", "09274", "09278", "09376"}

# From a6502c: Beschäftigte am Wohnort per Kreis (approximate)
EMPLOYED_AT_WOHNORT = {
    "09273": 48830,  # Kelheim
    "09375": 73000,  # Regensburg LK
    "09176": 51000,  # Eichstätt
    "09362": 93000,  # Regensburg Stadt
    "09373": 60000,  # Neumarkt
    "09186": 50000,  # Pfaffenhofen
    "09274": 57000,  # Landshut LK
    "09278": 27000,  # Straubing-Bogen
    "09376": 51000,  # Schwandorf
}


def test_parse_pendler_matrix():
    """Parse official BA Pendler data into Kreis->Kreis OD shares."""
    from bavaria.gravity.pendler_data import parse_pendler_matrix

    df_pendler = parse_pendler_matrix(str(PENDLER_PATH), STUDY_KREISE, EMPLOYED_AT_WOHNORT)

    # Should have columns: origin_kreis, destination_kreis, share
    assert set(df_pendler.columns) == {"origin_kreis", "destination_kreis", "share"}

    # Shares per origin should sum to ~1.0 (including internal + outside)
    for kreis in STUDY_KREISE:
        origin_shares = df_pendler[df_pendler["origin_kreis"] == kreis]["share"].sum()
        assert abs(origin_shares - 1.0) < 0.01, f"Shares for {kreis} sum to {origin_shares}, expected ~1.0"

    # Kelheim -> Regensburg Stadt should be a significant cross-Kreis flow
    kh_to_reg = df_pendler[
        (df_pendler["origin_kreis"] == "09273") & (df_pendler["destination_kreis"] == "09362")
    ]["share"].values[0]
    assert kh_to_reg > 0.10, f"Kelheim->Reg.St share should be significant, got {kh_to_reg}"

    # Internal share (same Kreis) should exist and be positive
    kh_internal = df_pendler[
        (df_pendler["origin_kreis"] == "09273") & (df_pendler["destination_kreis"] == "09273")
    ]["share"].values[0]
    assert kh_internal > 0.3, f"Kelheim internal share should be >30%, got {kh_internal}"

    # "_outside" destination should capture flows to non-study Kreise
    kh_outside = df_pendler[
        (df_pendler["origin_kreis"] == "09273") & (df_pendler["destination_kreis"] == "_outside")
    ]["share"].values[0]
    assert kh_outside > 0.0, "Should have some outside flows"


def test_parse_pendler_matrix_no_file():
    """Should raise if file doesn't exist."""
    from bavaria.gravity.pendler_data import parse_pendler_matrix
    with pytest.raises(FileNotFoundError):
        parse_pendler_matrix("/nonexistent.xlsx", {"09273"}, {"09273": 48830})
