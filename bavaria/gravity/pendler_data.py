"""
Parse BA Pendlerverflechtungen (Kreis-level) into origin->destination shares.
Load Beschaeftigte am Wohnort from a6502c for internal share computation.
"""
import pandas as pd
import numpy as np
from pathlib import Path


def parse_pendler_matrix(excel_path, study_kreise, employed_at_wohnort):
    """
    Parse BA Pendlerverflechtungen into Kreis->Kreis probability shares.

    Args:
        excel_path: Path to krpend-k-0-YYYYMM-xlsx.xlsx
        study_kreise: Set of 5-digit Kreis codes in the study area
        employed_at_wohnort: Dict {kreis_code: count} -- total SV-pflichtig
            Beschaeftigte with residence in that Kreis (= internal + Auspendler).
            Source: a6502c "Beschaeftigte am Wohnort" column.

    Returns:
        DataFrame [origin_kreis, destination_kreis, share]
        - Includes internal flows (origin == destination)
        - Includes "_outside" sentinel for destinations outside study area
        - Shares sum to 1.0 per origin
    """
    if not Path(excel_path).exists():
        raise FileNotFoundError(f"Pendler file not found: {excel_path}")

    df_raw = pd.read_excel(excel_path, sheet_name="Auspendler Kreise",
                           header=None, skiprows=6)
    df_raw.columns = ["wohn_code", "wohn_name", "arb_code", "arb_name",
                       "total", "male", "female", "german", "foreign", "unknown"]

    df_raw["wohn_code"] = df_raw["wohn_code"].ffill().astype(str).str.strip()

    # Keep rows with 5-digit destination codes (= Kreis level)
    df = df_raw.dropna(subset=["arb_code"]).copy()
    df["arb_code"] = df["arb_code"].astype(str).str.strip()
    df["total"] = pd.to_numeric(df["total"], errors="coerce").fillna(0)
    df = df[df["arb_code"].str.len() == 5].copy()
    df = df[df["wohn_code"].isin(study_kreise)].copy()

    rows = []
    for origin in sorted(study_kreise):
        wohnort_total = employed_at_wohnort.get(origin, 0)
        if wohnort_total == 0:
            rows.append({"origin_kreis": origin, "destination_kreis": origin, "share": 1.0})
            continue

        # All cross-Kreis flows from this origin (5-digit destinations, excluding self)
        cross_flows = df[(df["wohn_code"] == origin) & (df["arb_code"] != origin)]

        # Split into study-area and outside
        for _, row in cross_flows.iterrows():
            dest = row["arb_code"]
            share = row["total"] / wohnort_total
            if dest in study_kreise:
                rows.append({"origin_kreis": origin, "destination_kreis": dest, "share": share})
            # Outside flows accumulated below

        # Outside share = sum of all cross-Kreis flows to non-study Kreise
        outside_flows = cross_flows[~cross_flows["arb_code"].isin(study_kreise)]
        outside_share = outside_flows["total"].sum() / wohnort_total
        if outside_share > 0:
            rows.append({"origin_kreis": origin, "destination_kreis": "_outside", "share": outside_share})

        # Internal share = 1 - sum(all cross-Kreis) / wohnort_total
        auspendler_total = cross_flows["total"].sum()
        internal_share = max(0.0, 1.0 - auspendler_total / wohnort_total)
        rows.append({"origin_kreis": origin, "destination_kreis": origin, "share": internal_share})

    result = pd.DataFrame(rows)

    # Aggregate duplicate rows (shouldn't happen but safety)
    result = result.groupby(["origin_kreis", "destination_kreis"])["share"].sum().reset_index()

    # Renormalize: employed_at_wohnort may be approximate, so ensure shares sum to 1.0
    totals = result.groupby("origin_kreis")["share"].transform("sum")
    result["share"] = result["share"] / totals

    return result


def load_employed_at_wohnort(a6502c_path, study_kreise):
    """
    Load Beschaeftigte am Wohnort (employed residents) per Kreis from a6502c.

    Uses the same parsing approach as bavaria.data.census.employees:
    forward-fill Kreis headers, distinguish kreisfrei cities from Landkreise.

    Args:
        a6502c_path: Path to a6502c_YYYYMM.xlsx (Bavarian employment statistics)
        study_kreise: Set of 5-digit Kreis codes (e.g. {"09273", "09362"})

    Returns:
        Dict {kreis_code: employed_at_wohnort_count}
    """
    df = pd.read_excel(a6502c_path, sheet_name="Gemeinden", header=None, skiprows=8,
                        names=["code", "name", "unknown1", "arbeitsort", "male",
                               "foreigners", "s4", "s5", "s6", "s7", "s8",
                               "wohnort", "saldo"])
    df["code"] = df["code"].astype(str).str.strip()

    # Remove text footer (lines starting with "*)")
    end_idx = df["code"].str.startswith("*)").idxmax() if df["code"].str.startswith("*)").any() else len(df)
    df = df.iloc[:end_idx].copy()

    # Remove "Insgesamt" totals
    df = df[df["name"].astype(str) != "Insgesamt"].copy()

    # Identify data rows (have numeric arbeitsort)
    df["has_data"] = pd.to_numeric(df["arbeitsort"], errors="coerce").notna()

    # Kreisfreie cities: have data AND "(Krfr" in name
    df["is_kreisfrei"] = df["has_data"] & df["name"].astype(str).str.contains("Krfr", na=False)

    # Forward-fill Kreis code from non-data rows (Kreis headers)
    df["kreis"] = np.nan
    df.loc[~df["has_data"], "kreis"] = df["code"]
    df["kreis"] = df["kreis"].ffill()

    # Kreisfreie cities don't belong to a Landkreis — they ARE the Kreis
    df.loc[df["is_kreisfrei"], "kreis"] = np.nan

    # Keep only data rows
    df = df[df["has_data"]].copy()
    df["wohnort"] = pd.to_numeric(df["wohnort"], errors="coerce").fillna(0)

    # Build 5-digit Kreis AGS
    # Kreisfreie: "09" + city_code (e.g. "362" -> "09362")
    # Landkreise Gemeinden: "09" + kreis_code (e.g. kreis="273" -> "09273")
    df["kreis_5"] = np.where(
        df["kreis"].isna(),
        "09" + df["code"],  # kreisfreie: city code IS the Kreis code
        "09" + df["kreis"]  # Landkreise: use forward-filled Kreis header
    )

    # Aggregate by Kreis
    kreis_wohnort = df.groupby("kreis_5")["wohnort"].sum()

    result = {}
    for kreis in study_kreise:
        result[kreis] = int(kreis_wohnort.get(kreis, 0))

    return result
