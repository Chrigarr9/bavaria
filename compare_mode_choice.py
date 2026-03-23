"""Compare mode choice: eqasim 30km 100% vs calibrated 25% Kelheim scenario."""
import pandas as pd
import numpy as np
import pickle
import subprocess
import os

SIM_DIR = "C:/matsim_cache/matsim.simulation.run__c8188c4e525e61b67ce3cc9fea1cd0eb.cache/simulation_output"
REF_MODE_SHARE = "C:/Users/VWAUCCY/dev/msf/projects/Dissertation/matsim_scenarios/matsim-kelheim/src/main/resources/kelheim_mode_share.csv"
REF_MODE_PER_DIST = "C:/Users/VWAUCCY/dev/msf/projects/Dissertation/matsim_scenarios/matsim-kelheim/src/main/R/tidied-mode-share-per-distance.csv"

# --- Load eqasim trips ---
print("Loading eqasim trips...")
eq = pd.read_csv(f"{SIM_DIR}/eqasim_trips.csv", sep=";")
print(f"  Total trips: {len(eq):,}")
print(f"  Modes: {eq['mode'].value_counts().to_dict()}")

# Mode mapping: eqasim uses 'bicycle', 'car_passenger'; ref uses 'bike', 'ride'
mode_map = {'bicycle': 'bike', 'car_passenger': 'ride'}
eq['mode_mapped'] = eq['mode'].replace(mode_map)

# --- Overall mode share comparison ---
eq_share = eq['mode_mapped'].value_counts(normalize=True).sort_index()

# Reference: aggregate from per-distance data
ref = pd.read_csv(REF_MODE_SHARE)
ref_share = ref.groupby('main_mode')['share'].sum().sort_index()

print("\n" + "="*60)
print("OVERALL MODE SHARE COMPARISON")
print("="*60)
print(f"{'Mode':<12} {'25% Calibrated':>15} {'100% eqasim':>15} {'Delta':>10}")
print("-"*60)
all_modes = sorted(set(eq_share.index) | set(ref_share.index))
for m in all_modes:
    r = ref_share.get(m, 0)
    e = eq_share.get(m, 0)
    delta = e - r
    print(f"{m:<12} {r:>14.1%} {e:>14.1%} {delta:>+9.1%}")

# --- Mode share per distance bin ---
# Assign distance bins to eqasim trips
bins = [0, 1000, 2000, 5000, 10000, 20000, float('inf')]
labels = ['0 - 1000', '1000 - 2000', '2000 - 5000', '5000 - 10000', '10000 - 20000', '20000+']
eq['dist_bin'] = pd.cut(eq['euclidean_distance'], bins=bins, labels=labels, right=False)

# Compute mode share within each distance bin
eq_per_dist = eq.groupby(['dist_bin', 'mode_mapped']).size().unstack(fill_value=0)
eq_per_dist_share = eq_per_dist.div(eq_per_dist.sum(axis=1), axis=0)

# Reference per-distance shares (already in the right format)
ref_per_dist = ref.pivot(index='dist_group', columns='main_mode', values='share')
# Normalize to within-bin shares
ref_per_dist_norm = ref_per_dist.div(ref_per_dist.sum(axis=1), axis=0)

print("\n" + "="*80)
print("MODE SHARE BY DISTANCE BIN")
print("="*80)

for dist_label in labels:
    print(f"\n--- {dist_label}m ---")
    print(f"  {'Mode':<12} {'25% Calibrated':>15} {'100% eqasim':>15} {'Delta':>10}")
    for m in ['walk', 'bike', 'car', 'ride', 'pt']:
        r = ref_per_dist_norm.loc[dist_label, m] if dist_label in ref_per_dist_norm.index and m in ref_per_dist_norm.columns else 0
        e = eq_per_dist_share.loc[dist_label, m] if dist_label in eq_per_dist_share.index and m in eq_per_dist_share.columns else 0
        delta = e - r
        print(f"  {m:<12} {r:>14.1%} {e:>14.1%} {delta:>+9.1%}")

# --- Trip count by distance ---
print("\n" + "="*60)
print("TRIP COUNT BY DISTANCE BIN")
print("="*60)
dist_counts = eq['dist_bin'].value_counts().sort_index()
for d, c in dist_counts.items():
    pct = c / len(eq) * 100
    print(f"  {str(d):<20} {c:>10,} ({pct:>5.1f}%)")

# --- Prepare data for plotting ---
plot_data = {
    'eq_share': eq_share.to_dict(),
    'ref_share': ref_share.to_dict(),
    'eq_per_dist_share': {str(k): v.to_dict() for k, v in eq_per_dist_share.iterrows()},
    'ref_per_dist_norm': {str(k): v.to_dict() for k, v in ref_per_dist_norm.iterrows()},
    'labels': labels,
    'modes': ['walk', 'bike', 'car', 'ride', 'pt'],
}

with open('output/kelheim_30km_100pct/mode_comparison_data.pkl', 'wb') as f:
    pickle.dump(plot_data, f)
print("\nPlot data saved.")

# --- Generate plot ---
plot_script = r'''
import pickle
import numpy as np

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

with open('output/kelheim_30km_100pct/mode_comparison_data.pkl', 'rb') as f:
    data = pickle.load(f)

eq_share = data['eq_share']
ref_share = data['ref_share']
eq_per_dist = data['eq_per_dist_share']
ref_per_dist = data['ref_per_dist_norm']
labels = data['labels']
modes = data['modes']

mode_colors = {'walk': '#2ecc71', 'bike': '#e67e22', 'car': '#e74c3c', 'ride': '#9b59b6', 'pt': '#3498db'}

fig, axes = plt.subplots(2, 1, figsize=(14, 12), gridspec_kw={'height_ratios': [1, 2]})

# --- Top: Overall mode share ---
ax = axes[0]
x = np.arange(len(modes))
width = 0.35
ref_vals = [ref_share.get(m, 0) for m in modes]
eq_vals = [eq_share.get(m, 0) for m in modes]

bars1 = ax.bar(x - width/2, ref_vals, width, label='25% Calibrated (Senozon)',
               color=[mode_colors[m] for m in modes], alpha=0.6, edgecolor='black', linewidth=0.5)
bars2 = ax.bar(x + width/2, eq_vals, width, label='100% eqasim (30km)',
               color=[mode_colors[m] for m in modes], alpha=1.0, edgecolor='black', linewidth=0.5)

for bar, val in zip(bars1, ref_vals):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.005, f'{val:.1%}',
            ha='center', va='bottom', fontsize=9, color='#555')
for bar, val in zip(bars2, eq_vals):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.005, f'{val:.1%}',
            ha='center', va='bottom', fontsize=9, fontweight='bold')

ax.set_xticks(x)
ax.set_xticklabels([m.capitalize() for m in modes], fontsize=11)
ax.set_ylabel('Mode Share', fontsize=11)
ax.set_title('Overall Mode Share Comparison', fontsize=13, fontweight='bold')
ax.legend(fontsize=10)
ax.set_ylim(0, max(max(ref_vals), max(eq_vals)) * 1.2)
ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f'{y:.0%}'))
ax.grid(axis='y', alpha=0.3)

# --- Bottom: Mode share by distance ---
ax2 = axes[1]
n_bins = len(labels)
n_modes = len(modes)
bar_width = 0.08
group_width = n_modes * bar_width * 2 + 0.05

for i, m in enumerate(modes):
    ref_vals_d = [ref_per_dist.get(d, {}).get(m, 0) for d in labels]
    eq_vals_d = [eq_per_dist.get(d, {}).get(m, 0) for d in labels]

    x_pos = np.arange(n_bins)
    offset_ref = (i - n_modes/2) * bar_width * 2 - bar_width/2
    offset_eq = offset_ref + bar_width

    ax2.bar(x_pos + offset_ref, ref_vals_d, bar_width,
            color=mode_colors[m], alpha=0.4, edgecolor='grey', linewidth=0.3)
    ax2.bar(x_pos + offset_eq, eq_vals_d, bar_width,
            color=mode_colors[m], alpha=1.0, edgecolor='black', linewidth=0.3,
            label=m.capitalize() if i < n_modes else None)

ax2.set_xticks(np.arange(n_bins))
ax2.set_xticklabels([l + 'm' for l in labels], fontsize=9, rotation=15)
ax2.set_ylabel('Mode Share (within distance bin)', fontsize=11)
ax2.set_title('Mode Share by Euclidean Distance\n(faded = 25% calibrated, solid = 100% eqasim)', fontsize=13, fontweight='bold')
ax2.legend(fontsize=10, ncol=5, loc='upper right')
ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f'{y:.0%}'))
ax2.grid(axis='y', alpha=0.3)
ax2.set_ylim(0, 1.0)

plt.tight_layout()
plt.savefig('output/kelheim_30km_100pct/mode_comparison.png', dpi=150, bbox_inches='tight', facecolor='white')
print('Saved to output/kelheim_30km_100pct/mode_comparison.png')
plt.close()
'''

with open('output/kelheim_30km_100pct/_plot_mode.py', 'w') as f:
    f.write(plot_script)

winpython = r'C:\Programs\WPy64-31131\python-3.11.3.amd64\python.exe'
print(f'Running plot with {winpython}...')
result = subprocess.run([winpython, 'output/kelheim_30km_100pct/_plot_mode.py'],
                        capture_output=True, text=True, cwd=os.getcwd())
print(result.stdout)
if result.stderr:
    print('STDERR:', result.stderr[:500])
print(f'Plot exit code: {result.returncode}')
