"""Compare model commuter OD flows against official BA Pendlerverflechtungen."""
import pickle
import pandas as pd
import numpy as np
import sys
from pathlib import Path

_base = Path("C:/Users/VWAUCCY/dev/msf/projects/Dissertation/matsim_scenarios/bavaria")
sys.path.insert(0, str(_base))

# 1. Load model data
with open("C:/matsim_cache/bavaria.homes__1027fc93c3a33204886f2b56ed450559.p", "rb") as f:
    df_homes = pickle.load(f)

with open("C:/matsim_cache/bavaria.locations.synthesis.replacement__f2888d7417941bfda630779d0eb3665e.p", "rb") as f:
    data = pickle.load(f)
df_work_loc = data[0]

base = _base
df_persons = pd.read_csv(
    base / "output/kelheim_30km_100pct/kelheim_30km_100pct_persons.csv",
    delimiter=";", usecols=["person_id", "household_id"]
)

df_person_home = df_persons.merge(
    df_homes[["household_id", "commune_id"]].rename(columns={"commune_id": "home_commune"}),
    on="household_id"
)
df_work = df_work_loc[["person_id", "commune_id"]].rename(columns={"commune_id": "work_commune"})
df_commutes = df_person_home.merge(df_work, on="person_id")

df_commutes["home_kreis"] = df_commutes["home_commune"].astype(str).str[:5]
df_commutes["work_kreis"] = df_commutes["work_commune"].astype(str).str[:5]

model_flows = df_commutes.groupby(["home_kreis", "work_kreis"]).size().reset_index(name="model_count")
print(f"Total work commuters in model: {model_flows['model_count'].sum():,}")

# 2. Load official Pendler data
study_kreise = {"09273", "09375", "09176", "09362", "09373", "09186", "09274", "09278", "09376"}
from bavaria.gravity.pendler_data import parse_pendler_matrix, load_employed_at_wohnort

a6502c_path = str(base / "data/bavaria/a6502c_202200.xlsx")
pendler_path = str(base / "data/germany/krpend-k-0-202306-xlsx.xlsx")

wohnort = load_employed_at_wohnort(a6502c_path, study_kreise)
official_shares = parse_pendler_matrix(pendler_path, study_kreise, wohnort)
official_shares["official_count"] = official_shares.apply(
    lambda r: int(round(r["share"] * wohnort.get(r["origin_kreis"], 0))), axis=1
)

kreis_names = {
    "09273": "Kelheim (LK)",
    "09375": "Regensburg (LK)",
    "09176": "Eichstaett (LK)",
    "09362": "Regensburg (Stadt)",
    "09373": "Neumarkt (LK)",
    "09186": "Pfaffenhofen (LK)",
    "09274": "Landshut (LK)",
    "09278": "Straubing-Bogen",
    "09376": "Schwandorf (LK)",
}

# 3. Merge model and official
df_compare = model_flows.rename(columns={"home_kreis": "origin_kreis", "work_kreis": "destination_kreis"})
df_compare = df_compare[df_compare["origin_kreis"].isin(study_kreise)]

model_inside = df_compare[df_compare["destination_kreis"].isin(study_kreise)].copy()
model_outside = df_compare[~df_compare["destination_kreis"].isin(study_kreise)].copy()
model_outside_agg = model_outside.groupby("origin_kreis")["model_count"].sum().reset_index()
model_outside_agg["destination_kreis"] = "_outside"

model_all = pd.concat([model_inside, model_outside_agg], ignore_index=True)

merged = model_all.merge(
    official_shares[["origin_kreis", "destination_kreis", "official_count"]],
    on=["origin_kreis", "destination_kreis"],
    how="outer",
).fillna(0)

merged["model_count"] = merged["model_count"].astype(int)
merged["official_count"] = merged["official_count"].astype(int)
merged["diff"] = merged["model_count"] - merged["official_count"]
merged["pct_diff"] = np.where(
    merged["official_count"] > 0,
    merged["diff"] / merged["official_count"] * 100,
    np.nan,
)

# 4. Print results
print()
print("=" * 95)
print("ABSOLUTE COMMUTER OD COMPARISON: Model vs Official BA Pendlerverflechtungen")
print("=" * 95)

for origin in sorted(study_kreise):
    origin_data = merged[merged["origin_kreis"] == origin].copy()
    origin_name = kreis_names.get(origin, origin)
    model_total = origin_data["model_count"].sum()
    official_total = origin_data["official_count"].sum()

    print(f"\nFrom {origin} ({origin_name}):  Model={model_total:,}  Official={official_total:,}  Diff={model_total-official_total:+,}")
    print(f'  {"Destination":<30} {"Model":>8} {"Official":>8} {"Diff":>8} {"% Diff":>8}')
    print(f'  {"-"*30} {"-"*8} {"-"*8} {"-"*8} {"-"*8}')

    for _, row in origin_data.sort_values("official_count", ascending=False).iterrows():
        dest = row["destination_kreis"]
        dest_name = kreis_names.get(dest, dest) if dest != "_outside" else "Outside study area"
        pct = f'{row["pct_diff"]:+.1f}%' if not np.isnan(row["pct_diff"]) else "N/A"
        print(f"  {dest_name:<30} {int(row['model_count']):>8,} {int(row['official_count']):>8,} {int(row['diff']):>+8,} {pct:>8}")

# Overall stats
print()
print("=" * 95)
print("OVERALL STATISTICS")
print("=" * 95)
valid = merged[(merged["model_count"] > 0) | (merged["official_count"] > 0)]
valid_inside = valid[valid["destination_kreis"] != "_outside"]
pearson = valid_inside["model_count"].corr(valid_inside["official_count"])
print(f"Pearson r (absolute counts, in-study OD pairs): {pearson:.4f}")

rmse = np.sqrt(((valid_inside["model_count"] - valid_inside["official_count"]) ** 2).mean())
print(f"RMSE: {rmse:,.0f}")
mae = (valid_inside["model_count"] - valid_inside["official_count"]).abs().mean()
print(f"MAE:  {mae:,.0f}")

print(f'\nTotal model commuters (in study area):    {valid_inside["model_count"].sum():,}')
print(f'Total official commuters (in study area):  {valid_inside["official_count"].sum():,}')

diag = merged[merged["origin_kreis"] == merged["destination_kreis"]]
print(f"\nInternal commuters (diagonal):")
print(f"  Model:    {diag['model_count'].sum():,}")
print(f"  Official: {diag['official_count'].sum():,}")
print(f"  Diff:     {diag['model_count'].sum()-diag['official_count'].sum():+,}")

print(f"\nBeschaeftigte am Wohnort (official):")
for k in sorted(wohnort):
    print(f"  {k} ({kreis_names[k]:<25}): {wohnort[k]:>8,}")
print(f'  {"Total":<32}: {sum(wohnort.values()):>8,}')
