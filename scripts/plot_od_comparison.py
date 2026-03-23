"""
Visual comparison of model OD matrix against official Pendlerrechnung data.
Generates scatter plots, heatmaps, and flow maps.
"""
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# ============================================================
# Load data
# ============================================================

mk = pd.read_csv("C:/tmp/gemeinde_model_shares.csv", dtype={"origin_id": str, "destination_id": str})

base = Path(__file__).parent.parent
df_verfl = pd.read_csv(base / "data/bavaria/2024_Verfl_L09.csv", sep=";", dtype=str)
df_iop = pd.read_csv(base / "data/bavaria/2024_IOP_Karte_L00.csv", sep=";", dtype=str)

from bavaria.gravity.pendler_data import load_total_auspendler
ausp = load_total_auspendler(str(base / "data/bavaria/19321-001r.xlsx"), set(mk["origin_id"]))

study = ["09176", "09186", "09273", "09274", "09278", "09362", "09373", "09375", "09376"]
model_gems = set(mk["origin_id"])

df_verfl = df_verfl[df_verfl["ARS"].str[:5].isin(study)].copy()
df_verfl["AUSP_AO"] = pd.to_numeric(df_verfl["AUSP_AO"], errors="coerce")
df_iop = df_iop[df_iop["ARS"].isin(model_gems)].copy()
df_iop["IOP"] = pd.to_numeric(df_iop["IOP"], errors="coerce")

names = {
    "09176": "Eichstatt", "09186": "Pfaffenhofen", "09273": "Kelheim",
    "09274": "Landshut LK", "09278": "Straubing-B.", "09362": "Reg. Stadt",
    "09373": "Neumarkt", "09375": "Reg. LK", "09376": "Schwandorf",
}

# ============================================================
# Build comparison dataframe
# ============================================================

# Official: IOP + within-study Verfl, using Eckzahlen denominator
rows_off = []
for gem in sorted(model_gems):
    iop_val = float(df_iop[df_iop["ARS"] == gem]["IOP"].sum())
    ausp_val = ausp.get(gem, 0)
    total = iop_val + ausp_val
    if total > 0:
        rows_off.append({"origin": gem, "dest": gem, "official_count": iop_val, "official_share": iop_val / total})

    gem_verfl = df_verfl[df_verfl["ARS"] == gem]
    for _, row in gem_verfl.iterrows():
        dest = row["ARS_AO"]
        count = row["AUSP_AO"]
        if pd.notna(count) and count > 0 and dest in model_gems:
            rows_off.append({"origin": gem, "dest": dest, "official_count": count,
                             "official_share": count / total if total > 0 else 0})

df_off = pd.DataFrame(rows_off)

# Model shares
comparison = []
for origin in sorted(model_gems):
    off_o = df_off[df_off["origin"] == origin]
    mod_o = mk[mk["origin_id"] == origin]
    all_d = (set(off_o["dest"]) | set(mod_o["destination_id"])) & model_gems
    for dest in all_d:
        m = float(mod_o[mod_o["destination_id"] == dest]["weight"].sum())
        o_row = off_o[off_o["dest"] == dest]
        o = float(o_row["official_share"].sum()) if len(o_row) > 0 else 0
        o_count = float(o_row["official_count"].sum()) if len(o_row) > 0 else 0
        if m > 0.001 or o > 0.001:
            comparison.append({
                "origin": origin, "dest": dest,
                "model": m, "official": o, "official_count": o_count,
                "same_gem": origin == dest,
                "origin_kreis": origin[:5], "dest_kreis": dest[:5],
            })

c = pd.DataFrame(comparison)

# ============================================================
# Figure 1: Scatter — all OD pairs
# ============================================================
fig, axes = plt.subplots(1, 3, figsize=(18, 5.5))

# 1a: All pairs
ax = axes[0]
internal = c[c["same_gem"]]
cross = c[~c["same_gem"]]
ax.scatter(cross["official"] * 100, cross["model"] * 100, s=8, alpha=0.3, c="steelblue", label="Cross-Gemeinde")
ax.scatter(internal["official"] * 100, internal["model"] * 100, s=30, alpha=0.7, c="orangered", label="Internal", zorder=5)
lim = max(ax.get_xlim()[1], ax.get_ylim()[1])
ax.plot([0, lim], [0, lim], "k--", lw=0.8, alpha=0.5)
ax.set_xlabel("Official share (%)")
ax.set_ylabel("Model share (%)")
ax.set_title(f"All Gemeinde OD pairs (r={c['model'].corr(c['official']):.3f})")
ax.legend(fontsize=9)
ax.set_aspect("equal")

# 1b: Cross-Gemeinde only (zoomed)
ax = axes[1]
ax.scatter(cross["official"] * 100, cross["model"] * 100, s=10, alpha=0.4, c="steelblue")
lim = 25
ax.plot([0, lim], [0, lim], "k--", lw=0.8, alpha=0.5)
ax.set_xlim(0, lim)
ax.set_ylim(0, lim)
ax.set_xlabel("Official share (%)")
ax.set_ylabel("Model share (%)")
ax.set_title(f"Cross-Gemeinde (r={cross['model'].corr(cross['official']):.3f})")
ax.set_aspect("equal")

# 1c: Internal only
ax = axes[2]
int_valid = internal[(internal["official"] > 0) & (internal["model"] > 0)]
ax.scatter(int_valid["official"] * 100, int_valid["model"] * 100, s=30, alpha=0.7, c="orangered")
for _, row in int_valid.iterrows():
    if row["official"] > 0.4 or abs(row["model"] - row["official"]) > 0.2:
        ax.annotate(names.get(row["origin"][:5], ""), (row["official"] * 100, row["model"] * 100),
                    fontsize=6, alpha=0.7)
lim = 100
ax.plot([0, lim], [0, lim], "k--", lw=0.8, alpha=0.5)
ax.set_xlabel("Official internal share (%)")
ax.set_ylabel("Model internal share (%)")
r_int = int_valid["model"].corr(int_valid["official"])
ax.set_title(f"Internal flows (r={r_int:.3f})")
ax.set_aspect("equal")

plt.tight_layout()
plt.savefig(str(base / "output/kelheim_30km_1pct/od_scatter_comparison.png"), dpi=150, bbox_inches="tight")
print("Saved od_scatter_comparison.png")

# ============================================================
# Figure 2: Kreis-level heatmaps (model vs official)
# ============================================================
sk_sorted = sorted(study)

# Aggregate to Kreis
kr_model = c.groupby(["origin_kreis", "dest_kreis"])["model"].sum()
kr_off = c.groupby(["origin_kreis", "dest_kreis"])["official"].sum()

fig, axes = plt.subplots(1, 3, figsize=(18, 5))

for idx, (title, data) in enumerate([
    ("Model", kr_model), ("Official (Pendlerrechnung)", kr_off), ("Difference (model - official)", None)
]):
    ax = axes[idx]
    mat = np.zeros((len(sk_sorted), len(sk_sorted)))
    for i, o in enumerate(sk_sorted):
        for j, d in enumerate(sk_sorted):
            if data is not None:
                mat[i, j] = data.get((o, d), 0) * 100
            else:
                mat[i, j] = (kr_model.get((o, d), 0) - kr_off.get((o, d), 0)) * 100

    if data is not None:
        im = ax.imshow(mat, cmap="YlOrRd", vmin=0, vmax=100)
    else:
        vmax = np.abs(mat).max()
        im = ax.imshow(mat, cmap="RdBu_r", vmin=-vmax, vmax=vmax)

    ax.set_xticks(range(len(sk_sorted)))
    ax.set_xticklabels([names[k][:6] for k in sk_sorted], rotation=45, ha="right", fontsize=8)
    ax.set_yticks(range(len(sk_sorted)))
    ax.set_yticklabels([names[k][:6] for k in sk_sorted], fontsize=8)

    for i in range(len(sk_sorted)):
        for j in range(len(sk_sorted)):
            val = mat[i, j]
            if abs(val) > 1:
                color = "white" if (data is not None and val > 50) or (data is None and abs(val) > vmax * 0.6) else "black"
                ax.text(j, i, f"{val:.0f}", ha="center", va="center", fontsize=7, color=color)

    ax.set_title(title, fontsize=10)
    plt.colorbar(im, ax=ax, shrink=0.8, label="%")

plt.suptitle("Kreis-level commuter flow shares (%)", fontsize=12, y=1.02)
plt.tight_layout()
plt.savefig(str(base / "output/kelheim_30km_1pct/od_kreis_heatmap.png"), dpi=150, bbox_inches="tight")
print("Saved od_kreis_heatmap.png")

# ============================================================
# Figure 3: Internal share by Gemeinde — bar chart per Kreis
# ============================================================
fig, axes = plt.subplots(3, 3, figsize=(16, 12))

for idx, k in enumerate(sk_sorted):
    ax = axes[idx // 3][idx % 3]
    kg = internal[internal["origin_kreis"] == k].copy()
    if len(kg) == 0:
        ax.set_title(names[k])
        continue

    kg = kg.sort_values("official", ascending=False).head(20)
    x = np.arange(len(kg))
    w = 0.35
    ax.bar(x - w / 2, kg["official"] * 100, w, label="Official", color="steelblue", alpha=0.8)
    ax.bar(x + w / 2, kg["model"] * 100, w, label="Model", color="orangered", alpha=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels([g[5:9] for g in kg["origin"]], rotation=60, fontsize=6)
    ax.set_ylabel("Internal share (%)")
    ax.set_title(f"{names[k]} (n={len(internal[internal['origin_kreis']==k])})")
    if idx == 0:
        ax.legend(fontsize=8)
    ax.set_ylim(0, 100)

plt.suptitle("Internal commuter share: Model vs Official (per Gemeinde, top 20)", fontsize=12)
plt.tight_layout()
plt.savefig(str(base / "output/kelheim_30km_1pct/od_internal_bars.png"), dpi=150, bbox_inches="tight")
print("Saved od_internal_bars.png")

# ============================================================
# Figure 4: Summary statistics
# ============================================================
fig, axes = plt.subplots(1, 2, figsize=(12, 5))

# 4a: Pearson r by Kreis
ax = axes[0]
kreis_r = []
for k in sk_sorted:
    kc = c[c["origin_kreis"] == k]
    if len(kc) > 2:
        kreis_r.append({"kreis": names[k], "r": kc["model"].corr(kc["official"]), "n": len(kc)})
kr_df = pd.DataFrame(kreis_r)
colors = plt.cm.Set2(np.linspace(0, 1, len(kr_df)))
bars = ax.barh(kr_df["kreis"], kr_df["r"], color=colors)
ax.set_xlabel("Pearson r")
ax.set_title("Gemeinde-level OD correlation by Kreis")
ax.set_xlim(0, 1)
ax.axvline(0.93, color="red", ls="--", lw=0.8, label=f"Overall r={c['model'].corr(c['official']):.3f}")
ax.legend()
for bar, r in zip(bars, kr_df["r"]):
    ax.text(bar.get_width() + 0.01, bar.get_y() + bar.get_height() / 2, f"{r:.3f}", va="center", fontsize=9)

# 4b: Outside fraction per Kreis
ax = axes[1]
outside_by_kreis = []
for k in sk_sorted:
    gems_in_k = [g for g in model_gems if g[:5] == k]
    iop_total = sum(float(df_iop[df_iop["ARS"] == g]["IOP"].sum()) for g in gems_in_k)
    ausp_total = sum(ausp.get(g, 0) for g in gems_in_k)
    # Model: what fraction was dropped as outside?
    mod_within = sum(float(mk[mk["origin_id"] == g]["weight"].sum()) for g in gems_in_k)
    outside_by_kreis.append({"kreis": names[k], "ausp_frac": ausp_total / (iop_total + ausp_total) * 100 if (iop_total + ausp_total) > 0 else 0})

ok_df = pd.DataFrame(outside_by_kreis)
ax.barh(ok_df["kreis"], ok_df["ausp_frac"], color="lightcoral")
ax.set_xlabel("Auspendler fraction (%)")
ax.set_title("Fraction of employed residents commuting out")
ax.set_xlim(0, 100)

plt.tight_layout()
plt.savefig(str(base / "output/kelheim_30km_1pct/od_summary.png"), dpi=150, bbox_inches="tight")
print("Saved od_summary.png")

plt.close("all")
print("\nDone! 4 plots saved to output/kelheim_30km_1pct/")
