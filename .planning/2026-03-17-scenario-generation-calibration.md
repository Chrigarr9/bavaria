# Bavaria eqasim Scenario Generation & Calibration — Session Log

> **Date:** 2026-03-17 to 2026-03-18
> **Author:** Christoph Garritsen, assisted by Claude Code
> **Context:** Dissertation — DRT ridepooling as PT alternative in rural areas (Kelheim, Bavaria)

---

## 1. Objective

Generate a calibrated synthetic population and MATSim scenario for the Kelheim region in Bavaria to support DRT demand extraction. The scenario must have:
- Realistic mode shares matching MiD 2017 Bavaria data
- Realistic trip distances matching MiD distributions
- Correct vehicle availability (driving license, car, bicycle)
- Coverage of all relevant surrounding Kreise for commuter flows

The existing eqasim Bavaria pipeline uses French household travel survey data (ENTD 2008) as its behavioral template, which produces unrealistic mobility patterns for rural Bavaria. This session documents the systematic identification and correction of these discrepancies.

---

## 2. Geographic Scope Selection

### Method
Computed border distances from Kelheim center (UTM32N: 709000, 5423000) to all 96 Bavarian Kreise using VG250 administrative boundary data (BKG). A Kreis is "within radius" if any part of its border intersects the radius circle.

### Analysis of radius options

| Radius | Kreise | Population | Key additions |
|-------:|-------:|-----------:|---------------|
| 30km | 9 | 1.32M | Regensburg Stadt, Straubing-Bogen, Schwandorf |
| 50km | 19 | 2.44M | + Ingolstadt, Cham, Dingolfing-Landau |
| 70km | 28 | 4.00M | + Nürnberg, Deggendorf, Dachau |
| 100km | 55 | 9.31M | + München, Augsburg, Passau |

### Decision
**30km radius selected** — 9 Kreise, ~1.32M population. Sufficient for DRT demand extraction in Kelheim while keeping compute manageable. Includes the critical Regensburg Stadt employment center.

#### Selected Kreise
| Code | Name | Population | Border distance |
|------|------|----------:|----------------:|
| 09273 | Kelheim (LK) | 126,539 | 0 km |
| 09375 | Regensburg (LK) | 200,264 | 6 km |
| 09176 | Eichstätt (LK) | 136,565 | 9 km |
| 09362 | Regensburg (Stadt) | 159,465 | 16 km |
| 09373 | Neumarkt i.d.OPf. | 139,277 | 17 km |
| 09186 | Pfaffenhofen a.d.Ilm | 132,966 | 18 km |
| 09274 | Landshut (LK) | 165,608 | 22 km |
| 09278 | Straubing-Bogen | 104,167 | 26 km |
| 09376 | Schwandorf | 152,284 | 28 km |

A map visualization was generated at `output/kelheim_radius_tiers.png` showing all radius options.

---

## 3. Data Sources Collected

### 3.1 MiD 2017 Bayern (Mobilität in Deutschland)

**Source:** Bayerisches Staatsministerium für Wohnen, Bau und Verkehr
**Files:**
- `data/calibration/mid2017_regionalbericht_bayern.pdf` (30 MB)
- `data/calibration/mid2017_kurzreport_bayern.pdf` (1.2 MB)
- `data/calibration/mid2017_reference_data.csv` (extracted modal splits)
- `data/calibration/mid2017_trip_characteristics.csv` (extracted trip lengths, purposes)

**Key reference values for Kelheim area (Niederbayern/Oberpfalz weighted):**

| Mode | Target share |
|------|:-----------:|
| Walk | 17% |
| Bike | 7% |
| Car (driver) | 54% |
| Car (passenger) | 16% |
| PT | 7% |

**Trip characteristics (Bayern 2017):**
- 3.2 trips per person per day
- Car mean distance: 15.6 km (routed), median 6.7 km
- Walk mean: 1.7 km, Bike mean: 3.9 km, PT mean: 23.1 km

### 3.2 BASt SVZ 2021 Traffic Counts

**Source:** Bundesanstalt für Straßenwesen
**Files:**
- `data/calibration/svz2021_autobahnen.xlsx` (7.3 MB)
- `data/calibration/svz2021_bundesstrassen.xlsx` (20 MB)
- `data/calibration/bast_counts_kelheim_area.csv` (extracted, 200 stations)

**Coverage:** 200 counting stations across 19 roads (A3, A6, A9, A92, A93, B8, B11, B13, B15, B16, B20, B22, B85, B299, B300, B301, B388). DTV range: 2,365–81,934 vehicles/day.

### 3.3 KBA Driving License Data

**Source:** Kraftfahrt-Bundesamt (FE4, January 2025)
**File:** `data/germany/fe4_2025.xlsx`

Used to fix the eqasim pipeline's license data issue (see Section 5.1). The 2025 file contains separate entries for all 9 Kreise, unlike the 2024 file which merged Landshut LK with Landshut Stadt and Straubing-Bogen with Straubing Stadt.

### 3.4 VGR Income Data

**Source:** Arbeitskreis VGR der Länder
**File:** `data/germany/vgrdl_r2b3_bs2024.xlsx`
**Extracted:** `data/calibration/income_per_capita_kreise.csv`

Verfügbares Einkommen pro Einwohner (2023): €28,159 (Schwandorf) to €31,150 (Pfaffenhofen). Bayern average: €31,524. Relatively uniform across the study area. Not currently used in the mode choice model (eqasim's income stage assigns zero income to all households).

### 3.5 VG250 Administrative Boundaries

**Source:** BKG (Bundesamt für Kartographie und Geodäsie)
**File:** `data/germany/vg250-ew_12-31.utm32s.gpkg.ebenen.zip`

Used for Gemeinde-level spatial analysis, municipality classification (urban/suburban/rural by population density), and trip-to-municipality assignment.

---

## 4. Scenario Generation Pipeline

### 4.1 Base pipeline

The eqasim Bavaria pipeline (`eqasim-org/bavaria`) generates synthetic populations from:
- **Census 2022** (Zensus): population by age, sex, employment per Gemeinde
- **ENTD 2008** (French HTS): activity chains, trip distances, mode choices, license status
- **IPF** (Iterative Proportional Fitting): matches ENTD persons to census marginals
- **Gravity model**: distributes commute flows between Gemeinden
- **Spatial assignment**: places activities at specific facility locations

### 4.2 Pipeline configuration

**Config file:** `config_kelheim_30km_10pct.yml`

Key parameters:
```yaml
sampling_rate: 0.10          # 10% sample for calibration
bavaria.licenses_path: germany/fe4_2025.xlsx  # Fixed KBA data
java_binary: C:/.../jdk-22.0.2+9/bin/java.exe

# Distance scaling (ENTD → MiD)
commute_distance_scale_work: 1.2
commute_distance_scale_education: 1.4
secondary_distance_scale: 1.4

# Gravity model (fixed for rural Bavaria)
gravity_slope: -0.1          # flatter than default -0.2
gravity_constant: -2.4
gravity_diagonal: 0.0        # no same-Gemeinde bonus (default was 1.0)
```

### 4.3 Output

**10% sample:** 127,745 agents, ~400k trips, 3.2 trips/person
**100% scenario:** 1,285,494 agents (generated earlier, at `output/kelheim_30km_100pct/`)

---

## 5. Issues Identified and Fixes Applied

### 5.1 Driving License Data — Merged Kreise in KBA 2024

**Problem:** The KBA FE4 2024 file merged license counts for Landshut LK (09274) under Landshut Stadt (09261), and Straubing-Bogen (09278) under Straubing Stadt (09263). The pipeline's `licenses.py` splits merged counts by population ratio, but when the kreisfreie Städte are not in the study area's `political_prefix`, the split fails silently — resulting in **near-zero license rates** (0.5%) for Landshut LK and Straubing-Bogen.

**Impact:** Only 1-2% car mode share in those two Kreise; 42% ride (car passenger) share.

**Fix:** Updated `bavaria.licenses_path` to use `germany/fe4_2025.xlsx` which contains separate entries for all 9 Kreise. No code changes needed — the mapping logic in `licenses.py` only fires for missing Kreise, and none are missing in the 2025 file.

**Result:** Adult license rate improved from 50.1% to 80.2%. Landshut/Straubing no longer broken. Still below MiD target (~90%) due to ENTD matching limitation.

### 5.2 ENTD Matching Limitation — License Rates

**Problem:** The eqasim pipeline matches each synthetic person to a French ENTD template person on `["sex", "age_class", "has_license"]`. French license rates (~80% adults) are lower than rural Bavarian rates (~90%+). The ENTD template pool lacks enough licensed persons to match all Bavarian demographics, so IPF compromises and under-assigns licenses.

**Impact:** Overall adult license rate of 80% instead of ~90%. Cannot be fully fixed without replacing ENTD with MiD microdata as the HTS source.

**Mitigation:** For the 100% scenario, a post-hoc patching script was prepared to inject MiD-based license rates by age group. For the 10% calibration sample, the 80% rate was accepted as sufficient.

### 5.3 Gravity Model — Over-Localization

**Problem:** The gravity model's default parameters (from Île-de-France calibration) had `diagonal=1.0`, which added a massive same-Gemeinde bonus. This caused **88% of work trips to stay within the same Gemeinde**, capping commute distances at the Gemeinde diameter (~3-5 km for rural Gemeinden).

**Diagnosis:**
- `friction = exp(slope * distance + constant) + diagonal * I`
- With `diagonal=1.0`: same-Gemeinde friction = 1.09, neighboring Gemeinde at 10km = 0.012 → 88% stay local
- With `diagonal=0.0`: same-Gemeinde friction = 0.09, proportional to neighbors → 22% stay local

**Fix:** Set `gravity_diagonal: 0.0` and `gravity_slope: -0.1` (flatter than default -0.2).

**Result:** Work commute mean distance improved from 5.8km to 10.5km (MiD target: 15.4km routed ≈ ~11km euclidean).

**Code changes:**
- `config_kelheim_30km_10pct.yml`: added `gravity_slope`, `gravity_constant`, `gravity_diagonal` parameters
- These parameters were already supported in `bavaria/gravity/model.py` via `context.config()`

### 5.4 Distance Scaling — ENTD to MiD

**Problem:** ENTD-derived trip distances are shorter than MiD Bayern targets. The ENTD distances are used as inputs to the spatial assignment, so scaling them slightly adjusts the generated output.

**Fix:** Added configurable scaling factors to the pipeline:
- `commute_distance_scale_work: 1.2` (ENTD work 13.0km → MiD 15.4km)
- `commute_distance_scale_education: 1.4` (ENTD 5.4km → MiD 7.8km)
- `secondary_distance_scale: 1.4` (ENTD ~7km → MiD ~10km)

**Code changes:**
- `synthesis/population/spatial/commute_distance.py`: added `commute_distance_scale_work` and `commute_distance_scale_education` config parameters, applies multiplication after ENTD matching
- `synthesis/population/spatial/secondary/distance_distributions.py`: added `secondary_distance_scale` config parameter, scales ENTD distance CDFs

### 5.5 Facility Assignment — Exclusive 1:1 Constraint

**Problem:** The primary location assignment (`locations.py`) assigns each worker to a unique facility with `f_available[selected_index] = False`. In Gemeinden with more workers than facilities (32.6% overflow), remaining workers get assigned to whatever facility is left — often near home, creating artificially short commutes. 25% of generated work trips were < 0.5 km.

**Diagnosis:**
- 37,789 workers need placement across 19,411 unique facilities
- 235 of 239 Gemeinden have more workers than available facility slots
- Worst case: Regensburg Stadt with 4,474 workers but only 1,293 unique facilities (71% overflow)

**Fix:** Implemented shared facility assignment weighted by employee count:
- Each person picks the facility closest to their target commute distance
- Facilities can host multiple workers, with a soft overcapacity penalty proportional to employee count
- Added `shared_facility_assignment: True` config parameter and `assign_shared_facilities()` function

**Code changes:** `synthesis/population/spatial/primary/locations.py`

**Note:** Due to synpp cache management issues, the shared facility assignment was applied as a standalone post-processing script (`fix_work_locations.py`) rather than through the pipeline. The pipeline code was updated but the cache was not invalidated during this session.

**Result:** Work trip < 0.5 km reduced from 25.0% to 4.1%. Mean commute distance 14.1 km (euclidean), median 8.3 km — closely matching MiD 2017 (15.4 km routed mean, 7.8 km routed median; accounting for the ~1.3x routing factor, this gives euclidean ~11.8/6.0 km).

### 5.6 Java Version — Wrong JDK on PATH

**Problem:** The default `java` on system PATH was Java 8, but the eqasim/MATSim jars require Java 11+. The pipeline's GTFS processing stage failed with `UnsupportedClassVersionError`.

**Fix:** Added `java_binary: C:/Users/VWAUCCY/dev/msf/.jdk/jdk-22.0.2+9/bin/java.exe` to the config.

---

## 6. Eqasim Mode Choice Parameters

### 6.1 Scoring Architecture

The eqasim pipeline uses **two separate scoring systems**:
- **DMC (Discrete Mode Choice):** Uses eqasim-specific utility estimators (`BavariaCarUtilityEstimator`, etc.) with hardcoded ASC values
- **MATSim planCalcScore:** Used for plan selection with all constants = 0 (placeholder)

The eqasim estimators are the behaviorally relevant ones. MATSim's scoring is irrelevant for mode choice.

### 6.2 Current ASC Values (uncalibrated)

From `BavariaModeParameters.buildDefault()`:

| Parameter | Value | Comment |
|-----------|------:|---------|
| `car.alpha_u` | +0.4 | Manually overridden from -0.20 |
| `walk.alpha_u` | +1.8 | Manually overridden from +1.69 |
| `pt.alpha_u` | 0.0 | Neutral |
| `bike.alpha_u` | -0.5 | Manually overridden from -2.93 |
| `carPassenger.alpha_u` | -1.4 | Close to estimated -1.71 |

### 6.3 Mode Availability

From `BavariaModeAvailability`:
- Walk, PT: always available
- Car driver: requires `carAvailability != "none"` AND `hasLicense != "no"`
- Car passenger: requires `carAvailability != "none"`
- Bike: requires `bicycleAvailability != "none"`

Population attributes (10% sample, adults 18+): 80.2% license rate, 81.8% car availability, 80.0% bike availability.

### 6.4 Standalone Mode Choice Tool

eqasim provides `RunStandaloneModeChoice` which runs DMC on all agents without QSim. This enables fast calibration:
- **Runtime:** ~23 minutes for 128k agents (10% sample)
- **Output:** `output_trips.csv` with mode, distances, travel times per trip
- **Free-flow travel times** used (no congestion feedback)

### 6.5 Uncalibrated Mode Shares vs MiD Target

From standalone DMC on the 10% sample (before facility fix):

| Mode | DMC result | MiD target | Delta |
|------|:---------:|:----------:|:-----:|
| Walk | 21.7% | 17% | +4.7% |
| Bike | 19.4% | 7% | +12.4% |
| Car | 35.6% | 54% | -18.4% |
| Ride | 20.3% | 16% | +4.3% |
| PT | 3.1% | 7% | -3.9% |

**Key gap:** Bike massively overestimated, car massively underestimated. Requires ASC calibration.

---

## 7. Municipality Classification

Classified 246 Gemeinden within the 30km radius by population density:

| Class | Threshold | Gemeinden | Population | Density avg |
|-------|----------:|----------:|-----------:|------------:|
| Urban | ≥ 500/km² | 9 | 246,877 | 847/km² |
| Suburban | 150-500/km² | 56 | 477,538 | 237/km² |
| Rural | < 150/km² | 181 | 592,720 | 79/km² |

Kreisfreie Städte (Regensburg) are classified as urban regardless of density.

Output: `output/kelheim_30km_100pct/municipality_classification.csv` and `.pkl`

---

## 8. Calibration Tools Identified

### 8.1 boptx

**Repository:** github.com/sebhoerl/boptx
**Purpose:** Black-box optimization framework for calibrating eqasim/MATSim scenarios
**Approach:** Sweep ASC values → run standalone DMC → compare mode shares to target → optimize
**Estimated runtime:** ~23 min per evaluation × ~50-100 evaluations = 19-38 hours on 10% sample

### 8.2 Cadyts

Built into MATSim. Calibrates agent plan selection against traffic counts (BASt data). Good for route choice, less direct for mode choice.

### 8.3 RunStandaloneModeChoice

eqasim's built-in tool for running DMC without QSim. Enables fast calibration iterations without the ~30 min/iteration QSim cost.

---

## 9. Files Created/Modified

### New files
| File | Purpose |
|------|---------|
| `config_kelheim_30km_100pct.yml` | Pipeline config for 100% 30km scenario |
| `config_kelheim_30km_10pct.yml` | Pipeline config for 10% calibration sample |
| `data/calibration/mid2017_reference_data.csv` | Extracted MiD mode shares |
| `data/calibration/mid2017_trip_characteristics.csv` | Extracted MiD trip characteristics |
| `data/calibration/bast_counts_kelheim_area.csv` | Extracted BASt traffic counts |
| `data/calibration/income_per_capita_kreise.csv` | VGR income per capita |
| `data/germany/fe4_2025.xlsx` | KBA license data (2025, separate Kreise) |
| `output/kelheim_radius_tiers.png` | Radius tier map visualization |
| `classify_municipalities.py` | Municipality urban/suburban/rural classification |
| `compare_mode_choice.py` | Mode share comparison tool |
| `fix_work_locations.py` | Standalone work location fix (shared facilities) |
| `plot_radius_tiers_static.py` | Map generation script |

### Modified pipeline files
| File | Change |
|------|--------|
| `synthesis/population/spatial/commute_distance.py` | Added configurable distance scaling |
| `synthesis/population/spatial/secondary/distance_distributions.py` | Added configurable distance scaling |
| `synthesis/population/spatial/primary/locations.py` | Added shared facility assignment |

### Generated scenarios
| Directory | Description |
|-----------|-------------|
| `output/kelheim_30km_100pct/` | 100% scenario (1.28M agents, 9 Kreise) |
| `output/kelheim_30km_10pct/` | 10% calibration sample (128k agents) |

---

## 10. Next Steps

1. **boptx calibration** — optimize ASCs to match MiD mode shares on the 10% sample
2. **Regenerate 100% scenario** with calibrated parameters + all pipeline fixes
3. **Validate** against BASt traffic counts and MiD distance distributions
4. **DRT demand extraction** on the calibrated 100% scenario
