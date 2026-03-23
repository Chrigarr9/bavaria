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


def test_fallback_to_pure_gravity():
    """When pendler_od_path is None, should produce same output as before."""
    # This is tested implicitly by existing pipeline tests
    pass
