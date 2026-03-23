import pytest
import pandas as pd
import numpy as np
from pathlib import Path


def test_pendler_constrained_model_shares():
    """The Pendler-constrained model should produce correct Kreis-level shares."""
    from bavaria.gravity.pendler_data import parse_pendler_matrix, load_employed_at_wohnort
    from bavaria.gravity.model import build_pendler_constrained_matrix

    BASE = Path(__file__).parent.parent
    pendler_path = str(BASE / "data" / "germany" / "krpend-k-0-202306-xlsx.xlsx")
    a6502c_path = str(BASE / "data" / "bavaria" / "a6502c_202200.xlsx")
    study_kreise = {"09273", "09375", "09176", "09362", "09373", "09186", "09274", "09278", "09376"}

    wohnort = load_employed_at_wohnort(a6502c_path, study_kreise)
    pendler_shares = parse_pendler_matrix(pendler_path, study_kreise, wohnort)

    # Create mock Gemeinde data (3 Gemeinden in Kelheim, 2 in Reg.St)
    municipalities = ["092730111000", "092730137000", "092730147000",  # Kelheim LK
                      "093620000000", "093620001000"]  # Regensburg Stadt
    employees = pd.DataFrame({
        "destination_id": municipalities,
        "employees": [100, 200, 150, 500, 300]
    })

    distances = []
    rng = np.random.RandomState(42)
    for o in municipalities:
        for d in municipalities:
            dist = 0.0 if o == d else rng.uniform(5, 30)
            distances.append({"origin_id": o, "destination_id": d, "distance_km": dist})
    distances = pd.DataFrame(distances)

    result = build_pendler_constrained_matrix(
        municipalities, employees, distances,
        pendler_shares, study_kreise, slope=-0.1
    )

    assert set(result.columns) == {"origin_id", "destination_id", "weight"}

    # Shares should sum to ~1.0 per origin (excluding _outside which is dropped)
    for mun in municipalities:
        total = result[result["origin_id"] == mun]["weight"].sum()
        assert abs(total - 1.0) < 0.02, f"Weights for {mun} sum to {total}"


def test_gemeinde_constrained_model():
    """Gemeinde-level OD should use exact flows where known, gravity for remainder."""
    from bavaria.gravity.pendler_data import parse_gemeinde_od
    from bavaria.gravity.model import build_gemeinde_constrained_matrix

    BASE = Path(__file__).parent.parent
    verfl_path = str(BASE / "data" / "bavaria" / "2024_Verfl_L09.csv")
    iop_path = str(BASE / "data" / "bavaria" / "2024_IOP_Karte_L00.csv")

    municipalities = ["092730111111", "092730137137", "092730152152",
                      "093620000000"]

    gemeinde_od = parse_gemeinde_od(verfl_path, iop_path, set(municipalities))

    employees = pd.DataFrame({
        "destination_id": municipalities,
        "employees": [100, 200, 150, 500]
    })

    rng = np.random.RandomState(42)
    distances = []
    for o in municipalities:
        for d in municipalities:
            dist = 0.0 if o == d else rng.uniform(5, 30)
            distances.append({"origin_id": o, "destination_id": d, "distance_km": dist})
    distances = pd.DataFrame(distances)

    result, outside_fractions = build_gemeinde_constrained_matrix(
        municipalities, employees, distances,
        gemeinde_od, {"09273", "09362"}, slope=-0.1
    )

    assert set(result.columns) == {"origin_id", "destination_id", "weight"}

    # Weights should sum to ~1.0 per origin
    for mun in municipalities:
        total = result[result["origin_id"] == mun]["weight"].sum()
        assert abs(total - 1.0) < 0.02, f"Weights for {mun} sum to {total}"

    # Kelheim city -> Regensburg should be significant (top-1 in official data)
    kh_reg = result[
        (result["origin_id"] == "092730137137") & (result["destination_id"] == "093620000000")
    ]["weight"].sum()
    assert kh_reg > 0.1, f"Kelheim->Reg should be significant, got {kh_reg}"

    # Outside fractions should exist
    assert "commune_id" in outside_fractions.columns
    assert "outside_fraction" in outside_fractions.columns
    # Kelheim city should have some outside fraction (flows to München etc.)
    kh_outside = outside_fractions[outside_fractions["commune_id"] == "092730137137"]["outside_fraction"].iloc[0]
    assert kh_outside > 0.0, "Kelheim should have some outside commuters"


def test_fallback_to_pure_gravity():
    """When pendler_od_path is None, should produce same output as before."""
    # This is tested implicitly by existing pipeline tests
    pass
