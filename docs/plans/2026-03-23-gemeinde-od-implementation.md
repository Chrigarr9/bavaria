# Gemeinde-Level OD Matrix — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add Gemeinde-level OD mode using Pendlerrechnung top-10 flows + IOP internal data + gravity fill, as a third option alongside pure gravity and Kreis-level Pendler.

**Architecture:** New parsers in `pendler_data.py` read the Verfl and IOP CSVs. A new `build_gemeinde_constrained_matrix()` in `model.py` combines exact Gemeinde flows (internal + top-10) with gravity fill for the tail. Config switch via `gemeinde_od_path` / `gemeinde_iop_path`. Returns same `(df_work, df_edu, df_outside)` interface.

**Tech Stack:** Python 3.11+, pandas, numpy. synpp pipeline. Data: `2024_Verfl_L09.csv`, `2024_IOP_Karte_L00.csv`.

**Key ARS/AGS mapping:** ARS is 12-digit (Bundesland 2 + RegBez 1 + Kreis 2 + Verband 4 + Gemeinde 3). AGS is 8-digit (no Verband). Convert: `ags = ars[:5] + ars[9:]`. The pipeline uses 12-digit ARS as `commune_id`.

---

## Task 1: Parse Gemeinde-level OD data (Verfl + IOP)

**Files:**
- Modify: `matsim_scenarios/bavaria/bavaria/gravity/pendler_data.py`
- Modify: `matsim_scenarios/bavaria/tests/test_pendler_data.py`

**Step 1: Write the failing test**

Add to `tests/test_pendler_data.py`:

```python
def test_parse_gemeinde_od():
    """Parse Pendlerrechnung Gemeinde-level top-10 Auspendler flows."""
    from bavaria.gravity.pendler_data import parse_gemeinde_od

    verfl_path = str(Path(__file__).parent.parent / "data" / "bavaria" / "2024_Verfl_L09.csv")
    iop_path = str(Path(__file__).parent.parent / "data" / "bavaria" / "2024_IOP_Karte_L00.csv")

    # Pipeline uses 12-digit ARS as commune_id
    study_municipalities = {"092730137137", "092730111111", "092730152152",
                            "093620000000", "093750174174"}

    result = parse_gemeinde_od(verfl_path, iop_path, study_municipalities)

    assert set(result.columns) == {"origin_id", "destination_id", "count", "source"}

    # Internal flows should exist (from IOP)
    kh_internal = result[
        (result["origin_id"] == "092730137137") & (result["destination_id"] == "092730137137")
    ]
    assert len(kh_internal) == 1
    assert kh_internal.iloc[0]["source"] == "iop"
    assert kh_internal.iloc[0]["count"] > 2000  # Kelheim city has significant internal commuters

    # Cross-Gemeinde flows should exist (from Verfl top-10)
    kh_to_reg = result[
        (result["origin_id"] == "092730137137") & (result["destination_id"] == "093620000000")
    ]
    assert len(kh_to_reg) == 1
    assert kh_to_reg.iloc[0]["source"] == "verfl"
    assert kh_to_reg.iloc[0]["count"] > 1000  # Kelheim->Regensburg is #1 destination

    # Outside flows should be captured
    kh_flows = result[result["origin_id"] == "092730137137"]
    outside = kh_flows[~kh_flows["destination_id"].isin(study_municipalities)]
    assert len(outside) > 0, "Should have some outside-study-area flows in top-10"
```

**Step 2: Run test to verify it fails**

Run: `cd matsim_scenarios/bavaria && python -m pytest tests/test_pendler_data.py::test_parse_gemeinde_od -v`
Expected: FAIL — `ImportError: cannot import name 'parse_gemeinde_od'`

**Step 3: Write implementation**

Add to `bavaria/gravity/pendler_data.py`:

```python
def parse_gemeinde_od(verfl_path, iop_path, study_municipalities):
    """
    Parse Pendlerrechnung Gemeinde-level OD data.

    Combines top-10 Auspendler flows (Verfl) with internal commuters (IOP).

    Args:
        verfl_path: Path to 2024_Verfl_L09.csv (top-10 Auspendler per Gemeinde)
        iop_path: Path to 2024_IOP_Karte_L00.csv (internal commuters)
        study_municipalities: Set of 12-digit ARS commune_ids in the study area

    Returns:
        DataFrame [origin_id, destination_id, count, source]
        where source is "verfl" (top-10 cross-Gemeinde) or "iop" (internal).
        Includes flows to destinations outside study_municipalities.
    """
    # Parse Verfl: top-10 Auspendler destinations per Gemeinde
    # Columns: Rang;ARS;ARS_AO;AUSP_AO;AUSP_km;ARS_WO;EIP_WO;EIP_km
    df_verfl = pd.read_csv(verfl_path, sep=";", dtype=str)
    df_verfl["AUSP_AO"] = pd.to_numeric(df_verfl["AUSP_AO"], errors="coerce")

    # Filter to study-area origins
    df_verfl = df_verfl[df_verfl["ARS"].isin(study_municipalities)].copy()

    rows = []
    for _, row in df_verfl.iterrows():
        if pd.notna(row["AUSP_AO"]) and row["AUSP_AO"] > 0:
            rows.append({
                "origin_id": row["ARS"],
                "destination_id": row["ARS_AO"],
                "count": row["AUSP_AO"],
                "source": "verfl",
            })

    # Parse IOP: internal commuters per Gemeinde
    df_iop = pd.read_csv(iop_path, sep=";", dtype=str)
    df_iop["IOP"] = pd.to_numeric(df_iop["IOP"], errors="coerce")
    df_iop = df_iop[df_iop["ARS"].isin(study_municipalities)]

    for _, row in df_iop.iterrows():
        if pd.notna(row["IOP"]) and row["IOP"] > 0:
            rows.append({
                "origin_id": row["ARS"],
                "destination_id": row["ARS"],
                "count": row["IOP"],
                "source": "iop",
            })

    return pd.DataFrame(rows)
```

**Step 4: Run test to verify it passes**

Run: `cd matsim_scenarios/bavaria && python -m pytest tests/test_pendler_data.py -v`
Expected: PASS (all tests including existing ones)

**Step 5: Commit**

```bash
git add bavaria/gravity/pendler_data.py tests/test_pendler_data.py
git commit -m "feat: add Gemeinde-level OD parser (Verfl top-10 + IOP internal)"
```

---

## Task 2: Build Gemeinde-constrained OD matrix

**Files:**
- Modify: `matsim_scenarios/bavaria/bavaria/gravity/model.py`
- Modify: `matsim_scenarios/bavaria/tests/test_pendler_gravity.py`

**Step 1: Write the failing test**

Add to `tests/test_pendler_gravity.py`:

```python
def test_gemeinde_constrained_model():
    """Gemeinde-level OD should use exact flows where known, gravity for remainder."""
    from bavaria.gravity.pendler_data import parse_gemeinde_od
    from bavaria.gravity.model import build_gemeinde_constrained_matrix

    BASE = Path(__file__).parent.parent
    verfl_path = str(BASE / "data" / "bavaria" / "2024_Verfl_L09.csv")
    iop_path = str(BASE / "data" / "bavaria" / "2024_IOP_Karte_L00.csv")

    # Use real study area municipalities from the pipeline
    study_kreise = {"09273", "09375", "09176", "09362", "09373", "09186", "09274", "09278", "09376"}

    # Create a small set of test municipalities (subset of real data)
    municipalities = ["092730111111", "092730137137", "092730152152",  # Kelheim LK
                      "093620000000"]  # Regensburg Stadt

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
        gemeinde_od, study_kreise, slope=-0.1
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
```

**Step 2: Run test to verify it fails**

Run: `cd matsim_scenarios/bavaria && python -m pytest tests/test_pendler_gravity.py::test_gemeinde_constrained_model -v`
Expected: FAIL — `ImportError: cannot import name 'build_gemeinde_constrained_matrix'`

**Step 3: Write implementation**

Add to `bavaria/gravity/model.py`:

```python
def build_gemeinde_constrained_matrix(municipalities, df_employees, df_distances,
                                       gemeinde_od, study_kreise, slope):
    """
    Build Gemeinde x Gemeinde OD matrix using exact flows where known + gravity fill.

    For each origin:
    1. Internal flow from IOP data
    2. Top-10 cross-Gemeinde flows from Verfl data (within study area)
    3. Gravity fill for remaining Auspendler (employee x distance decay,
       excluding already-assigned destinations)
    4. Outside flows tracked for population dropping

    Args:
        municipalities: List of 12-digit commune_ids
        df_employees: DataFrame [destination_id, employees]
        df_distances: DataFrame [origin_id, destination_id, distance_km]
        gemeinde_od: DataFrame from parse_gemeinde_od [origin_id, destination_id, count, source]
        study_kreise: Set of 5-digit Kreis codes
        slope: Gravity decay parameter

    Returns:
        (df_weights, df_outside) where:
        - df_weights: DataFrame [origin_id, destination_id, weight] summing to 1.0 per origin
        - df_outside: DataFrame [commune_id, outside_fraction]
    """
    mun_set = set(municipalities)

    # Index employees and distances
    emp_lookup = dict(zip(df_employees["destination_id"], df_employees["employees"]))
    dist_lookup = {}
    for _, row in df_distances.iterrows():
        dist_lookup[(row["origin_id"], row["destination_id"])] = row["distance_km"]

    # Group gemeinde_od by origin
    od_by_origin = {}
    for _, row in gemeinde_od.iterrows():
        od_by_origin.setdefault(row["origin_id"], []).append(row)

    weight_rows = []
    outside_rows = []

    for origin in municipalities:
        flows = od_by_origin.get(origin, [])

        # Separate known flows
        internal_count = 0
        known_within = {}  # dest -> count (within study area, not self)
        outside_count = 0

        for f in flows:
            dest = f["destination_id"]
            count = f["count"]
            if dest == origin:
                internal_count += count
            elif dest in mun_set:
                known_within[dest] = known_within.get(dest, 0) + count
            else:
                outside_count += count

        total_known = internal_count + sum(known_within.values()) + outside_count

        # Gravity fill for remaining (destinations not in top-10, within study area)
        known_dests = set(known_within.keys()) | {origin}
        fill_dests = [m for m in municipalities if m != origin and m not in known_dests]

        gravity_weights = {}
        for dest in fill_dests:
            emp = emp_lookup.get(dest, 0)
            dist = dist_lookup.get((origin, dest), 50.0)
            w = emp * np.exp(slope * dist)
            if w > 0:
                gravity_weights[dest] = w

        # Estimate how much flow goes to gravity-fill destinations
        # Gravity fill gets: whatever is not accounted for by known flows
        # We don't have total Auspendler in this function, so we scale gravity
        # proportional to the known cross-Gemeinde flows
        known_cross_total = sum(known_within.values())
        # Heuristic: top-10 captures ~73% of Auspendler on average
        # So gravity fill gets ~27% of cross-Gemeinde flows
        # But more precisely: gravity_fill_count ≈ known_cross_total * (1 - coverage) / coverage
        # We use 0.37 as the fill ratio (27% / 73%)
        FILL_RATIO = 0.37
        gravity_fill_total = known_cross_total * FILL_RATIO

        # Build raw weights (counts)
        raw = {}
        raw[origin] = internal_count  # internal

        for dest, count in known_within.items():
            raw[dest] = count  # known cross-Gemeinde

        # Add gravity fill
        grav_total = sum(gravity_weights.values())
        if grav_total > 0 and gravity_fill_total > 0:
            for dest, gw in gravity_weights.items():
                raw[dest] = raw.get(dest, 0) + gravity_fill_total * (gw / grav_total)

        # Compute outside fraction
        total_within = sum(raw.values())
        total_all = total_within + outside_count
        outside_frac = outside_count / total_all if total_all > 0 else 0.0

        # Normalize within-study weights to 1.0
        if total_within > 0:
            for dest, w in raw.items():
                weight_rows.append({
                    "origin_id": origin,
                    "destination_id": dest,
                    "weight": w / total_within,
                })
        else:
            # Fallback: self-flow
            weight_rows.append({
                "origin_id": origin,
                "destination_id": origin,
                "weight": 1.0,
            })

        outside_rows.append({
            "commune_id": origin,
            "outside_fraction": outside_frac,
        })

    df_weights = pd.DataFrame(weight_rows)
    df_outside = pd.DataFrame(outside_rows)

    return df_weights, df_outside
```

**Step 4: Run test to verify it passes**

Run: `cd matsim_scenarios/bavaria && python -m pytest tests/test_pendler_gravity.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add bavaria/gravity/model.py tests/test_pendler_gravity.py
git commit -m "feat: add Gemeinde-constrained OD matrix builder"
```

---

## Task 3: Wire into pipeline execute() and config

**Files:**
- Modify: `matsim_scenarios/bavaria/bavaria/gravity/model.py` (execute + configure)
- Modify: `matsim_scenarios/bavaria/config_kelheim_30km_1pct.yml`
- Modify: `matsim_scenarios/bavaria/config_kelheim_30km_10pct.yml`
- Modify: `matsim_scenarios/bavaria/config_kelheim_30km_100pct.yml`

**Step 1: Update configure() to declare new config options**

In `model.py`, add to `configure()`:
```python
context.config("gemeinde_od_path", None)
context.config("gemeinde_iop_path", None)
```

**Step 2: Update execute() with three-way switch**

Replace the current if/else in `execute()`:

```python
gemeinde_od_path = context.config("gemeinde_od_path")
pendler_od_path = context.config("pendler_od_path")

if gemeinde_od_path is not None:
    # === GEMEINDE-LEVEL OD MODE ===
    from bavaria.gravity.pendler_data import parse_gemeinde_od

    data_path = context.config("data_path")
    full_verfl_path = "{}/{}".format(data_path, gemeinde_od_path)
    full_iop_path = "{}/{}".format(data_path, context.config("gemeinde_iop_path"))

    study_kreise = set(m[:5] for m in municipalities)
    print(f"Gemeinde-level OD mode: {len(municipalities)} Gemeinden")

    gemeinde_od = parse_gemeinde_od(full_verfl_path, full_iop_path, set(municipalities))

    slope = context.config("gravity_slope")
    df_work_matrix, df_outside = build_gemeinde_constrained_matrix(
        municipalities,
        df_employees.reset_index() if "destination_id" not in df_employees.columns else df_employees,
        df_distances.reset_index() if "origin_id" not in df_distances.columns else df_distances,
        gemeinde_od, study_kreise, slope
    )

    # ... (outside fraction logging, education gravity, return 3-tuple)

elif pendler_od_path is not None:
    # === KREIS-LEVEL PENDLER MODE (existing) ===
    # ... (unchanged)

else:
    # === PURE GRAVITY MODE (existing) ===
    # ... (unchanged)
```

**Step 3: Update config files**

Add to all three configs (1pct, 10pct, 100pct) after `pendler_od_path`:
```yaml
  # Gemeinde-level Pendlerrechnung OD data (null = use Kreis-level Pendler or gravity)
  gemeinde_od_path: bavaria/2024_Verfl_L09.csv
  gemeinde_iop_path: bavaria/2024_IOP_Karte_L00.csv
```

**Step 4: Run 1% pipeline and validate**

Run: `cd matsim_scenarios/bavaria && python -m synpp config_kelheim_30km_1pct.yml`

Validate Gemeinde-level Pearson r > 0.80 using the comparison script from earlier.

**Step 5: Commit**

```bash
git add bavaria/gravity/model.py config_kelheim_30km_*.yml
git commit -m "feat: wire Gemeinde-level OD into pipeline with three-way config switch"
```

---

## Implementation Warnings

**ARS code matching:** The Verfl CSV uses 12-digit ARS codes (same as pipeline `commune_id`). The IOP CSV also uses 12-digit ARS. No conversion needed — but verify the codes match the pipeline's `commune_id` values by checking a few examples.

**FILL_RATIO heuristic:** The 0.37 ratio (27%/73%) is based on our analysis showing top-10 captures ~73% of Auspendler. This is a study-area average. Per-Gemeinde coverage varies (38-88%). A more precise approach would load the Eckzahlen file to get exact total Auspendler per Gemeinde — but that adds complexity for marginal improvement. Start with the heuristic and refine if validation shows issues.

**Outside fraction:** The Gemeinde-level outside fraction is computed directly from the Verfl data (flows to non-study destinations). This replaces the Kreis-level outside fraction computation. The `pendler_od_path` config is still accepted but not used in Gemeinde mode — it serves as documentation and fallback.

**Cache invalidation:** Adding `gemeinde_od_path` to `configure()` changes the config hash, so synpp will automatically recompute the gravity model stage and all downstream stages. Upstream stages (census, OSM, IPF, etc.) are reused from cache.
