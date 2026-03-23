"""
Spatial map of OD model quality — choropleth maps showing per-Gemeinde
model vs official comparison on the study area map.
"""
import pandas as pd
import numpy as np
import geopandas as gpd
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from matplotlib.lines import Line2D
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# ============================================================
# Load data
# ============================================================

base = Path(__file__).parent.parent
gdf = gpd.read_file("C:/tmp/study_area_gemeinden.gpkg")
gdf = gdf.rename(columns={"commune_id": "gem"})

mk = pd.read_csv("C:/tmp/gemeinde_model_shares.csv", dtype={"origin_id": str, "destination_id": str})
df_verfl = pd.read_csv(base / "data/bavaria/2024_Verfl_L09.csv", sep=";", dtype=str)
df_iop = pd.read_csv(base / "data/bavaria/2024_IOP_Karte_L00.csv", sep=";", dtype=str)

from bavaria.gravity.pendler_data import load_total_auspendler
model_gems = set(mk["origin_id"])
ausp = load_total_auspendler(str(base / "data/bavaria/19321-001r.xlsx"), model_gems)

study = ["09176", "09186", "09273", "09274", "09278", "09362", "09373", "09375", "09376"]
names = {
    "09176": "Eichstatt", "09186": "Pfaffenhofen", "09273": "Kelheim",
    "09274": "Landshut LK", "09278": "Straubing-Bogen", "09362": "Regensburg Stadt",
    "09373": "Neumarkt", "09375": "Regensburg LK", "09376": "Schwandorf",
}

df_verfl = df_verfl[df_verfl["ARS"].str[:5].isin(study)].copy()
df_verfl["AUSP_AO"] = pd.to_numeric(df_verfl["AUSP_AO"], errors="coerce")
df_iop = df_iop[df_iop["ARS"].isin(model_gems)].copy()
df_iop["IOP"] = pd.to_numeric(df_iop["IOP"], errors="coerce")

# ============================================================
# Compute per-Gemeinde metrics
# ============================================================

rows = []
for gem in sorted(model_gems):
    iop_val = float(df_iop[df_iop["ARS"] == gem]["IOP"].sum())
    ausp_val = ausp.get(gem, 0)
    total = iop_val + ausp_val

    # Official internal share
    off_int = iop_val / total if total > 0 else 0

    # Model internal share
    mod_int = float(mk[(mk["origin_id"] == gem) & (mk["destination_id"] == gem)]["weight"].sum())

    # Internal gap
    gap_int = mod_int - off_int

    # Outside fraction (Auspendler / total)
    outside_frac = ausp_val / total if total > 0 else 0

    # Cross-Gemeinde correlation for this origin
    mod_o = mk[mk["origin_id"] == gem]
    off_o = df_verfl[df_verfl["ARS"] == gem]

    # Top destination match: does model's #1 dest match official's #1?
    mod_top = mod_o[mod_o["destination_id"] != gem].sort_values("weight", ascending=False)
    off_top = off_o[off_o["ARS_AO"].isin(model_gems) & (off_o["ARS_AO"] != gem)].sort_values("AUSP_AO", ascending=False)

    top1_match = False
    if len(mod_top) > 0 and len(off_top) > 0:
        top1_match = mod_top.iloc[0]["destination_id"] == off_top.iloc[0]["ARS_AO"]

    rows.append({
        "gem": gem, "kreis": gem[:5],
        "iop": iop_val, "ausp": ausp_val, "total": total,
        "off_int": off_int, "mod_int": mod_int, "gap_int": gap_int,
        "outside_frac": outside_frac, "top1_match": top1_match,
    })

df_metrics = pd.DataFrame(rows)
gdf = gdf.merge(df_metrics, on="gem", how="left")

# Kreis boundaries for overlay
kreis_bounds = gdf.dissolve(by="kreis").reset_index()

# ============================================================
# Figure: 2x2 map grid
# ============================================================

fig, axes = plt.subplots(2, 2, figsize=(16, 16))

# --- Map 1: Internal share gap (model - official) ---
ax = axes[0][0]
vmax = 0.5
gdf.plot(column="gap_int", cmap="RdBu_r", vmin=-vmax, vmax=vmax,
         linewidth=0.3, edgecolor="gray", ax=ax, legend=False)
kreis_bounds.boundary.plot(ax=ax, linewidth=1.5, edgecolor="black")
sm = plt.cm.ScalarMappable(cmap="RdBu_r", norm=mcolors.Normalize(-vmax, vmax))
cb = fig.colorbar(sm, ax=ax, shrink=0.6, label="Model - Official (pp)")
cb.set_ticks([-0.4, -0.2, 0, 0.2, 0.4])
cb.set_ticklabels(["-40pp", "-20pp", "0", "+20pp", "+40pp"])
ax.set_title("Internal share gap (model - official)", fontsize=12, fontweight="bold")
ax.set_axis_off()
# Add Kreis labels
for _, row in kreis_bounds.iterrows():
    centroid = row.geometry.centroid
    ax.text(centroid.x, centroid.y, names.get(row["kreis"], "")[:8],
            ha="center", va="center", fontsize=7, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.2", fc="white", alpha=0.7))

# --- Map 2: Official internal share ---
ax = axes[0][1]
gdf.plot(column="off_int", cmap="YlOrRd", vmin=0, vmax=0.8,
         linewidth=0.3, edgecolor="gray", ax=ax, legend=False)
kreis_bounds.boundary.plot(ax=ax, linewidth=1.5, edgecolor="black")
sm = plt.cm.ScalarMappable(cmap="YlOrRd", norm=mcolors.Normalize(0, 0.8))
cb = fig.colorbar(sm, ax=ax, shrink=0.6, label="Internal share")
cb.set_ticks([0, 0.2, 0.4, 0.6, 0.8])
cb.set_ticklabels(["0%", "20%", "40%", "60%", "80%"])
ax.set_title("Official internal share (IOP / total employed)", fontsize=12, fontweight="bold")
ax.set_axis_off()
for _, row in kreis_bounds.iterrows():
    centroid = row.geometry.centroid
    ax.text(centroid.x, centroid.y, names.get(row["kreis"], "")[:8],
            ha="center", va="center", fontsize=7, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.2", fc="white", alpha=0.7))

# --- Map 3: Outside fraction (Auspendler / total) ---
ax = axes[1][0]
gdf.plot(column="outside_frac", cmap="YlGnBu", vmin=0, vmax=1.0,
         linewidth=0.3, edgecolor="gray", ax=ax, legend=False)
kreis_bounds.boundary.plot(ax=ax, linewidth=1.5, edgecolor="black")
sm = plt.cm.ScalarMappable(cmap="YlGnBu", norm=mcolors.Normalize(0, 1))
cb = fig.colorbar(sm, ax=ax, shrink=0.6, label="Auspendler fraction")
cb.set_ticks([0, 0.25, 0.5, 0.75, 1.0])
cb.set_ticklabels(["0%", "25%", "50%", "75%", "100%"])
ax.set_title("Auspendler fraction (commuting OUT of Gemeinde)", fontsize=12, fontweight="bold")
ax.set_axis_off()
for _, row in kreis_bounds.iterrows():
    centroid = row.geometry.centroid
    ax.text(centroid.x, centroid.y, names.get(row["kreis"], "")[:8],
            ha="center", va="center", fontsize=7, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.2", fc="white", alpha=0.7))

# --- Map 4: Top-1 destination match ---
ax = axes[1][1]
gdf["top1_color"] = gdf["top1_match"].map({True: 1.0, False: 0.0}).fillna(0.5)
cmap_match = mcolors.ListedColormap(["#d73027", "#91bfdb", "#4575b4"])
bounds = [-0.5, 0.25, 0.75, 1.5]
norm = mcolors.BoundaryNorm(bounds, cmap_match.N)
gdf.plot(column="top1_color", cmap=cmap_match, norm=norm,
         linewidth=0.3, edgecolor="gray", ax=ax, legend=False)
kreis_bounds.boundary.plot(ax=ax, linewidth=1.5, edgecolor="black")
legend_elements = [
    Line2D([0], [0], marker="s", color="w", markerfacecolor="#4575b4", markersize=12, label="Match"),
    Line2D([0], [0], marker="s", color="w", markerfacecolor="#d73027", markersize=12, label="Mismatch"),
    Line2D([0], [0], marker="s", color="w", markerfacecolor="#91bfdb", markersize=12, label="No data"),
]
ax.legend(handles=legend_elements, loc="lower left", fontsize=9)
ax.set_title("Top-1 outbound destination: model vs official", fontsize=12, fontweight="bold")
ax.set_axis_off()
for _, row in kreis_bounds.iterrows():
    centroid = row.geometry.centroid
    ax.text(centroid.x, centroid.y, names.get(row["kreis"], "")[:8],
            ha="center", va="center", fontsize=7, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.2", fc="white", alpha=0.7))

n_match = gdf["top1_match"].sum()
n_total = gdf["top1_match"].notna().sum()
fig.suptitle(f"Gemeinde-level OD Model Quality Assessment — {len(model_gems)} Gemeinden, 9 Kreise\n"
             f"Top-1 destination match: {n_match}/{n_total} ({n_match/n_total:.0%})",
             fontsize=14, fontweight="bold", y=0.98)

plt.tight_layout(rect=[0, 0, 1, 0.96])
out_path = str(base / "output/kelheim_30km_1pct/od_spatial_quality.png")
plt.savefig(out_path, dpi=150, bbox_inches="tight")
print(f"Saved {out_path}")
plt.close()
