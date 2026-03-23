"""Reassign work locations in an existing MATSim population XML.
Uses distance-based matching with shared facilities (weighted by employee count).
Replaces the 1:1 exclusive assignment that caused 25% of work trips < 0.5km.

Run with bavaria env python from the bavaria/ directory.
"""
import gzip
import re
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
import pickle
import glob
import os
import sys

# --- Configuration ---
POPULATION_IN = "output/kelheim_30km_10pct/kelheim_30km_10pct_population.xml.gz"
POPULATION_OUT = "output/kelheim_30km_10pct/kelheim_30km_10pct_population_fixedwork.xml.gz"

print("=== Fix Work Locations: Shared Facility Assignment ===")
print(f"Input:  {POPULATION_IN}")
print(f"Output: {POPULATION_OUT}")

# --- Load work facilities with employee weights ---
fac_caches = sorted(glob.glob("C:/matsim_cache/bavaria.locations.work__*.p"), key=os.path.getmtime, reverse=True)
work_fac = pickle.load(open(fac_caches[0], 'rb'))
print(f"\nWork facilities: {len(work_fac):,}")
print(f"Total employees: {work_fac['employees'].sum():,.0f}")

fac_coords = np.vstack([work_fac.geometry.x.values, work_fac.geometry.y.values]).T
fac_employees = np.maximum(work_fac["employees"].values, 1).astype(float)
fac_x = work_fac.geometry.x.values
fac_y = work_fac.geometry.y.values

# --- Load commute distance targets ---
commute_caches = sorted(glob.glob("C:/matsim_cache/synthesis.population.spatial.commute_distance__*.p"), key=os.path.getmtime, reverse=True)
commute_data = pickle.load(open(commute_caches[0], 'rb'))
work_commute = commute_data['work'].set_index('person_id')['commute_distance']
print(f"Commute distances loaded for {len(work_commute):,} persons")

# --- Load home locations ---
home_caches = sorted(glob.glob("C:/matsim_cache/synthesis.population.spatial.home.locations__*.p"), key=os.path.getmtime, reverse=True)
homes = pickle.load(open(home_caches[0], 'rb'))
home_coords = {}
for _, row in homes.iterrows():
    home_coords[row['household_id']] = (row.geometry.x, row.geometry.y)
print(f"Home locations loaded for {len(home_coords):,} households")

# --- Load person -> household mapping ---
person_caches = sorted(glob.glob("C:/matsim_cache/bavaria.synthesis.population.enriched__*.p"), key=os.path.getmtime, reverse=True)
persons_df = pickle.load(open(person_caches[0], 'rb'))
person_to_hh = dict(zip(persons_df['person_id'].astype(str), persons_df['household_id']))

# --- Assign facilities ---
print("\nAssigning work facilities (distance-based, employee-weighted)...")

fac_usage = np.zeros(len(work_fac))
assignments = {}  # person_id -> (x, y, facility_id)
n_assigned = 0
n_no_commute = 0

for pid_str, hh_id in person_to_hh.items():
    pid_int = int(pid_str)
    if pid_int not in work_commute.index:
        continue

    target_dist = work_commute[pid_int]
    if pd.isna(target_dist) or target_dist <= 0:
        n_no_commute += 1
        continue

    if hh_id not in home_coords:
        continue

    home_x, home_y = home_coords[hh_id]
    distances = np.sqrt((fac_coords[:, 0] - home_x)**2 + (fac_coords[:, 1] - home_y)**2)

    # Cost = distance mismatch + soft overcapacity penalty
    distance_cost = np.abs(distances - target_dist)
    overcapacity = np.maximum(0, fac_usage - fac_employees) / fac_employees
    cost = distance_cost + overcapacity * target_dist * 0.1

    best_idx = np.argmin(cost)
    fac_usage[best_idx] += 1

    assignments[pid_str] = (fac_x[best_idx], fac_y[best_idx])
    n_assigned += 1

    if n_assigned % 5000 == 0:
        print(f"  assigned {n_assigned:,}...")

print(f"Assigned: {n_assigned:,}, no commute distance: {n_no_commute:,}")

# --- Patch the population XML ---
print(f"\nPatching population XML...")

n_patched = 0
n_persons = 0

with gzip.open(POPULATION_IN, 'rt', encoding='utf-8') as fin, \
     gzip.open(POPULATION_OUT, 'wt', encoding='utf-8') as fout:

    current_person_id = None
    first_work_found = False

    for line in fin:
        # Track person ID
        pid_match = re.search(r'<person id="(\d+)"', line)
        if pid_match:
            current_person_id = pid_match.group(1)
            first_work_found = False
            n_persons += 1

        # Find first work activity and replace only the x= and y= coordinate attributes
        # Use space-preceding match to avoid hitting facility= or other attributes
        if current_person_id and current_person_id in assignments and not first_work_found:
            if 'type="work"' in line and '<activity' in line:
                new_x, new_y = assignments[current_person_id]
                line = re.sub(r'(\s)x="[^"]*"', rf'\1x="{new_x}"', line)
                line = re.sub(r'(\s)y="[^"]*"', rf'\1y="{new_y}"', line)
                first_work_found = True
                n_patched += 1

        if '</person>' in line:
            current_person_id = None

        fout.write(line)

print(f"Persons: {n_persons:,}, work locations patched: {n_patched:,}")
print(f"Output: {POPULATION_OUT}")
print(f"File size: {os.path.getsize(POPULATION_OUT) / 1024 / 1024:.1f} MB")
