"""Quick validation of Pendler-constrained gravity model output."""
import pickle, glob
import pandas as pd
import numpy as np
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bavaria.gravity.pendler_data import parse_pendler_matrix, load_employed_at_wohnort

# Load the gravity model caches - pick the newest one
cache_files = sorted(glob.glob("C:/matsim_cache_1pct/bavaria.gravity.model__*.p"))
print(f"Found {len(cache_files)} gravity caches, loading: {cache_files[-1]}")
with open(cache_files[-1], "rb") as f:
    df_work, df_edu = pickle.load(f)

print(f"Work matrix: {len(df_work)} rows, {df_work['origin_id'].nunique()} origins")

# Aggregate model output to Kreis level
df_work["origin_kreis"] = df_work["origin_id"].str[:5]
df_work["dest_kreis"] = df_work["destination_id"].str[:5]

model_kreis = df_work.groupby(["origin_kreis", "dest_kreis"])["weight"].sum().reset_index()
totals = model_kreis.groupby("origin_kreis")["weight"].transform("sum")
model_kreis["share"] = model_kreis["weight"] / totals

# Load official
study_kreise = {"09273", "09375", "09176", "09362", "09373", "09186", "09274", "09278", "09376"}
base = Path(__file__).parent.parent
wohnort = load_employed_at_wohnort(str(base / "data/bavaria/a6502c_202200.xlsx"), study_kreise)
official = parse_pendler_matrix(str(base / "data/germany/krpend-k-0-202306-xlsx.xlsx"), study_kreise, wohnort)

# Compare
rows = []
for origin in sorted(study_kreise):
    for dest in sorted(study_kreise):
        m = model_kreis[(model_kreis["origin_kreis"] == origin) & (model_kreis["dest_kreis"] == dest)]["share"].sum()
        o = official[(official["origin_kreis"] == origin) & (official["destination_kreis"] == dest)]["share"].sum()
        if m > 0.005 or o > 0.005:
            rows.append({"origin": origin, "dest": dest, "model": m, "official": o})

df_cmp = pd.DataFrame(rows)
df_cmp["diff"] = df_cmp["model"] - df_cmp["official"]

r = df_cmp["model"].corr(df_cmp["official"])
print(f"\nPearson r (Kreis-level shares): {r:.4f}")
print(f"\nTop OD pairs by official share:")
for _, row in df_cmp.sort_values("official", ascending=False).head(15).iterrows():
    print(f'  {row["origin"]} -> {row["dest"]}: model={row["model"]:.3f}  official={row["official"]:.3f}  diff={row["diff"]:+.3f}')

print(f"\n=== Key metrics ===")
kh_reg = df_cmp[(df_cmp["origin"] == "09273") & (df_cmp["dest"] == "09362")]
if len(kh_reg):
    print(f'Kelheim -> Reg.St:  model={kh_reg.iloc[0]["model"]:.3f}  official={kh_reg.iloc[0]["official"]:.3f}')
kh_int = df_cmp[(df_cmp["origin"] == "09273") & (df_cmp["dest"] == "09273")]
if len(kh_int):
    print(f'Kelheim internal:   model={kh_int.iloc[0]["model"]:.3f}  official={kh_int.iloc[0]["official"]:.3f}')
reg_lk_st = df_cmp[(df_cmp["origin"] == "09375") & (df_cmp["dest"] == "09362")]
if len(reg_lk_st):
    print(f'Reg.LK -> Reg.St:   model={reg_lk_st.iloc[0]["model"]:.3f}  official={reg_lk_st.iloc[0]["official"]:.3f}')
