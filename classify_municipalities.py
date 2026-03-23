"""Classify municipalities as urban/rural and prepare spatial lookup for trip origins.
Exports a municipality-to-classification mapping for mode choice comparison.
Run with bavaria env python.
"""
import geopandas as gpd
import pandas as pd
import numpy as np
from shapely.geometry import Point
import pickle

print("Loading VG250 Gemeinde boundaries...")
gpkg = 'data/germany/tmp_vg250/vg250-ew_12-31.utm32s.gpkg.ebenen/vg250-ew_ebenen_1231/DE_VG250.gpkg'
gem = gpd.read_file(gpkg, layer='vg250_gem')
print(f"  Total Gemeinden: {len(gem)}")

# Filter to our 9 Kreise
kreise_30km = {'09273', '09375', '09176', '09362', '09373', '09186', '09274', '09278', '09376'}
gem['ars5'] = gem['ARS'].str[:5]
gem_30km = gem[gem['ars5'].isin(kreise_30km)].copy()
print(f"  Gemeinden in 30km radius: {len(gem_30km)}")

# Compute area in km² and population density
gem_30km['area_km2'] = gem_30km.geometry.area / 1e6
gem_30km['pop_density'] = gem_30km['EWZ'] / gem_30km['area_km2']

# Classification: use German BIK-style categories based on density
# Urban: > 500 pop/km² (cities, dense towns)
# Suburban: 150-500 pop/km²
# Rural: < 150 pop/km²
def classify_urban(row):
    if row['pop_density'] >= 500:
        return 'urban'
    elif row['pop_density'] >= 150:
        return 'suburban'
    else:
        return 'rural'

gem_30km['urban_class'] = gem_30km.apply(classify_urban, axis=1)

# Also classify kreisfreie Städte as urban by default
gem_30km.loc[gem_30km['BEZ'] == 'Kreisfreie Stadt', 'urban_class'] = 'urban'

print("\n--- Classification summary ---")
for cls in ['urban', 'suburban', 'rural']:
    subset = gem_30km[gem_30km['urban_class'] == cls]
    print(f"  {cls:>10}: {len(subset):>4} Gemeinden, pop={subset['EWZ'].sum():>10,}, "
          f"avg density={subset['pop_density'].mean():>6.0f} pop/km²")

print(f"\n  Total: {len(gem_30km)} Gemeinden, pop={gem_30km['EWZ'].sum():,}")

# Print some notable municipalities
print("\n--- Notable municipalities ---")
for _, row in gem_30km.nlargest(10, 'EWZ').iterrows():
    print(f"  {row['GEN']:<30} pop={row['EWZ']:>8,}  density={row['pop_density']:>6.0f}  class={row['urban_class']}")

# Prepare spatial index for point-in-polygon lookup
# Create centroid-based lookup too (faster for large datasets)
print("\nBuilding spatial index...")
gem_30km_sindex = gem_30km.sindex

# Export the classified municipalities data for the comparison script
# Keep only needed columns to reduce pickle size
export_cols = ['ARS', 'GEN', 'BEZ', 'EWZ', 'area_km2', 'pop_density', 'urban_class', 'ars5', 'geometry']
gem_export = gem_30km[export_cols].copy()

# Also export as a simple dict: ARS -> (name, class, kreis_code)
gem_lookup = {}
for _, row in gem_30km.iterrows():
    gem_lookup[row['ARS']] = {
        'name': row['GEN'],
        'class': row['urban_class'],
        'kreis': row['ars5'],
        'pop': row['EWZ'],
        'density': row['pop_density'],
    }

output = {
    'gem_gdf': gem_export,
    'gem_lookup': gem_lookup,
    'kreise_30km': kreise_30km,
}

out_path = 'output/kelheim_30km_100pct/municipality_classification.pkl'
with open(out_path, 'wb') as f:
    pickle.dump(output, f)
print(f"\nSaved to {out_path}")

# Also save a summary CSV for reference
summary = gem_30km[['ARS', 'GEN', 'BEZ', 'EWZ', 'area_km2', 'pop_density', 'urban_class', 'ars5']].copy()
summary.to_csv('output/kelheim_30km_100pct/municipality_classification.csv', index=False, sep=';')
print("Summary CSV saved.")
