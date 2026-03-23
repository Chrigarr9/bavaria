"""Generate static PNG map of Kelheim scenario radius tiers.
Step 1: Extract geometry data with bavaria env's geopandas, save as pickle.
Step 2: Plot with WinPython's matplotlib (which has working savefig).
Run this with the bavaria env python.
"""
import sys
import subprocess
import os

# Step 1: Extract and prepare data using this python (has geopandas)
import geopandas as gpd
import pandas as pd
import numpy as np
from shapely.geometry import Point
import pickle

print('Loading VG250 data...')
gpkg = 'data/germany/tmp_vg250/vg250-ew_12-31.utm32s.gpkg.ebenen/vg250-ew_ebenen_1231/DE_VG250.gpkg'
krs = gpd.read_file(gpkg, layer='vg250_krs')
bav = krs[krs['ARS'].str.startswith('09')].copy()

kelheim_center = Point(709000, 5423000)
bav['border_dist_km'] = bav.geometry.distance(kelheim_center) / 1000.0
bav['ars5'] = bav['ARS'].str[:5]

# Radii to use (must be sorted ascending)
radii = [30, 40, 50, 70, 100]
tier_names_ordered = ['kelheim'] + [f'{r}km' for r in radii]  # inner to outer


# Simple cumulative radius classification — if a Kreis intersects the circle, it's in
def classify(row):
    if row['ars5'] == '09273':
        return 'kelheim'
    for r in radii:
        if row['border_dist_km'] <= r:
            return f'{r}km'
    return 'outside'


bav['tier'] = bav.apply(classify, axis=1)

# Extract polygon data as simple lists for each tier
all_tiers = ['outside'] + list(reversed(tier_names_ordered))  # draw order: back to front
tier_data = {}
for tier_name in all_tiers:
    subset = bav[bav['tier'] == tier_name]
    polys = []
    for _, row in subset.iterrows():
        geom = row.geometry
        if geom.geom_type == 'MultiPolygon':
            for poly in geom.geoms:
                xs, ys = poly.exterior.coords.xy
                polys.append((list(xs), list(ys)))
        elif geom.geom_type == 'Polygon':
            xs, ys = geom.exterior.coords.xy
            polys.append((list(xs), list(ys)))
    tier_data[tier_name] = polys

# Labels: (name, centroid_x, centroid_y)
label_codes = {
    '09273': 'Kelheim',
    '09362': 'Regensburg',
    '09161': 'Ingolstadt',
    '09162': 'Munchen',
    '09564': 'Nurnberg',
    '09261': 'Landshut',
    '09263': 'Straubing',
    '09761': 'Augsburg',
    '09376': 'Schwandorf',
    '09372': 'Cham',
    '09271': 'Deggendorf',
    '09275': 'Passau',
    '09178': 'Freising',
    '09373': 'Neumarkt',
    '09371': 'Amberg',
    '09174': 'Dachau',
    '09576': 'Roth',
}
label_data = []
for ars5, name in label_codes.items():
    row = bav[bav['ars5'] == ars5]
    if len(row) > 0:
        c = row.geometry.centroid.iloc[0]
        bdist = row['border_dist_km'].iloc[0]
        label_data.append((name, c.x, c.y, ars5, bdist))

# Population summaries per radius
stats = {}
for r in radii:
    mask = bav['border_dist_km'] <= r
    stats[r] = {'pop': int(bav[mask]['EWZ'].sum()), 'n': int(mask.sum())}

data = {
    'tier_data': tier_data,
    'label_data': label_data,
    'kelheim_xy': (709000, 5423000),
    'radii': radii,
    'stats': stats,
    'all_tiers': all_tiers,
    'tier_names_ordered': tier_names_ordered,
}

pkl_path = 'output/tier_plot_data.pkl'
with open(pkl_path, 'wb') as f:
    pickle.dump(data, f)
print(f'Data saved to {pkl_path}')
for r in radii:
    s = stats[r]
    print(f'{r}km: {s["n"]} Kreise, {s["pop"]:,} pop')

# Step 2: Generate the plot using WinPython
plot_script = r'''
import pickle
import numpy as np

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import Polygon as MplPolygon
from matplotlib.collections import PatchCollection

with open('output/tier_plot_data.pkl', 'rb') as f:
    data = pickle.load(f)

tier_data = data['tier_data']
label_data = data['label_data']
kx, ky = data['kelheim_xy']
radii = data['radii']
stats = data['stats']
all_tiers = data['all_tiers']

# Color palette: inner (warm) to outer (cool)
tier_colors = {
    'kelheim':  ('#c0392b', '#7b241c'),
    '30km':     ('#e74c3c', '#922b21'),
    '40km':     ('#f39c12', '#b7791f'),
    '50km':     ('#f1c40f', '#b8960f'),
    '70km':     ('#3498db', '#1f6dad'),
    '100km':    ('#2ecc71', '#1a9c54'),
    'outside':  ('#ecf0f1', '#bdc3c7'),
}

fig, ax = plt.subplots(1, 1, figsize=(13, 15))

# Draw polygons per tier (back to front)
for tier_name in all_tiers:
    polys = tier_data.get(tier_name, [])
    if not polys:
        continue
    fc, ec = tier_colors[tier_name]
    patches = []
    for xs, ys in polys:
        verts = list(zip(xs, ys))
        patches.append(MplPolygon(verts, closed=True))
    pc = PatchCollection(patches, facecolor=fc, edgecolor=ec, linewidth=0.5)
    ax.add_collection(pc)

# Radius circles
for r in radii:
    ls = '-' if r <= 70 else '--'
    lw = 2.0 if r <= 70 else 1.2
    cc = '#2c3e50' if r <= 70 else '#7f8c8d'
    circle = plt.Circle((kx, ky), r * 1000, fill=False, edgecolor=cc,
                         linewidth=lw, linestyle=ls, zorder=5)
    ax.add_patch(circle)

# Kelheim center star
ax.plot(kx, ky, marker='*', color='white', markersize=22,
        markeredgecolor='black', markeredgewidth=1.5, zorder=10)

# City labels
bold_set = {'09273', '09162', '09564', '09362'}
for name, cx, cy, ars5, bdist in label_data:
    fs = 9.5 if ars5 in ('09162', '09564', '09761') else 7.5
    fw = 'bold' if ars5 in bold_set else 'normal'
    ax.annotate(name, xy=(cx, cy), fontsize=fs, fontweight=fw,
                ha='center', va='center',
                bbox=dict(boxstyle='round,pad=0.2', facecolor='white',
                          alpha=0.85, edgecolor='none'))

# Radius labels along NE direction
angle = np.radians(40)
for r in radii:
    x = kx + r * 1000 * np.cos(angle)
    y = ky + r * 1000 * np.sin(angle)
    ax.annotate(f'{r} km', xy=(x, y), fontsize=8, fontweight='bold',
                ha='center', va='bottom',
                bbox=dict(boxstyle='round,pad=0.2', facecolor='white',
                          alpha=0.9, edgecolor='#2c3e50'))

# Legend
legend_elements = [
    mpatches.Patch(facecolor=tier_colors['kelheim'][0],
                   edgecolor=tier_colors['kelheim'][1],
                   label='Kelheim LK (core) - 127k'),
]
prev_n = 1  # Kelheim itself
prev_r = 0
for r in radii:
    s = stats[r]
    fc, ec = tier_colors[f'{r}km']
    if prev_r == 0:
        label = f'Within {r}km ({s["n"]} Kreise) - {s["pop"]/1e6:.2f}M'
    else:
        label = f'{prev_r}-{r}km (+{s["n"] - stats[prev_r]["n"]} Kreise) - {s["pop"]/1e6:.2f}M total'
    legend_elements.append(mpatches.Patch(facecolor=fc, edgecolor=ec, label=label))
    prev_r = r

legend_elements.append(
    mpatches.Patch(facecolor=tier_colors['outside'][0],
                   edgecolor=tier_colors['outside'][1],
                   label='Rest of Bavaria'))
legend_elements.append(
    plt.Line2D([0], [0], marker='*', color='white', markeredgecolor='black',
               markersize=14, label='Kelheim center'))

ax.legend(handles=legend_elements, loc='lower left', fontsize=9,
          framealpha=0.95, title='Scenario Tiers (100% population)',
          title_fontsize=10)

ax.set_title(
    'Kelheim Extended Scenario - Landkreis Coverage by Radius\n'
    'Kreis included if any part of its border is within radius of Kelheim center',
    fontsize=13, fontweight='bold', pad=15)
ax.set_xlabel('Easting (m)', fontsize=10)
ax.set_ylabel('Northing (m)', fontsize=10)
ax.ticklabel_format(style='plain')
ax.set_aspect('equal')
ax.autoscale()

plt.tight_layout()
outpath = 'output/kelheim_radius_tiers.png'
plt.savefig(outpath, dpi=150, bbox_inches='tight', facecolor='white')
print(f'Saved to {outpath}')
plt.close()
'''

# Write the plotting script
plot_script_path = 'output/_plot_step2.py'
with open(plot_script_path, 'w') as f:
    f.write(plot_script)

# Run with WinPython
winpython = r'C:\Programs\WPy64-31131\python-3.11.3.amd64\python.exe'
print(f'Running plot with {winpython}...')
result = subprocess.run([winpython, plot_script_path],
                        capture_output=True, text=True, cwd=os.getcwd())
print(result.stdout)
if result.stderr:
    print('STDERR:', result.stderr)
print(f'Plot exit code: {result.returncode}')
