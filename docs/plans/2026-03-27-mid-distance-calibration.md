# MiD 2017 Bayern Distance Calibration — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace French ENTD distance CDFs with MiD 2017 Bayern CDFs in the eqasim pipeline's location choice, and add ring-based facility search, so the synthetic population matches observed Bavarian trip distance distributions.

**Architecture:** Add a `MiDDistanceSampler` that samples from MiD purpose-specific CDFs (inverse CDF via `np.interp`). Add `query_ring()` to `CandidateIndex` using KDTree `query_radius` for distance-band facility search. Update `commute_distance.py` to resample work/education distances from MiD CDFs. All gated behind `use_mid_distances: true` config flag for backward compatibility.

**Tech Stack:** Python, numpy, scikit-learn KDTree, eqasim-java pipeline framework

---

## MiD 2017 Bayern Reference CDFs

All CDFs: routed km from MiD Kurzreport Bayern. Convert to euclidean meters: `m = km / 1.3 * 1000`.

| Routed km | Euclidean m | work   | education | shop | leisure | other (all-trips) |
|-----------|-------------|--------|-----------|------|---------|-------------------|
| 0         | 0           | 0      | 0         | 0    | 0       | 0                 |
| 1         | 769         | .05    | .22       | .25  | .18     | .28               |
| 2         | 1538        | .13    | .38       | .44  | .30     | .41               |
| 5         | 3846        | .32    | .58       | .72  | .50     | .60               |
| 10        | 7692        | .53    | .77       | .89  | .66     | .76               |
| 20        | 15385       | .76    | .93       | .97  | .82     | .88               |
| 50        | 38462       | .95    | .99       | 1.0  | .95     | .97               |

Source: MiD 2017 Kurzreport Bayern (Bayerisches Staatsministerium), pp. 13/19 + Regionalbericht distance bands.

---

### Task 1: Add `MiDDistanceSampler` to components.py

**Files:**
- Modify: `matsim_scenarios/bavaria/synthesis/population/spatial/secondary/components.py`

**Step 1: Add MiDDistanceSampler class after CustomDistanceSampler (line 36)**

Append after the `CustomDistanceSampler` class (after line 36), before `CandidateIndex`:

```python
class MiDDistanceSampler(rda.FeasibleDistanceSampler):
    """Sample trip distances from MiD 2017 Bayern empirical CDFs per activity purpose.

    Replaces ENTD-based sampling with German survey data that matches
    Bavarian travel patterns. No purpose correction factors needed.

    Source: MiD 2017 Kurzreport Bayern (Bayerisches Staatsministerium)
    CDFs: routed km converted to euclidean meters (routed / 1.3 * 1000).
    """

    # (euclidean_meters, cumulative_probability) per purpose
    _DISTANCES_M = np.array([0, 769, 1538, 3846, 7692, 15385, 38462])

    _CDFS = {
        "shop":      np.array([0, .25, .44, .72, .89, .97, 1.0]),
        "leisure":   np.array([0, .18, .30, .50, .66, .82, .95]),
        "other":     np.array([0, .28, .41, .60, .76, .88, .97]),
    }

    def __init__(self, random, maximum_iterations=1000):
        super().__init__(random=random, maximum_iterations=maximum_iterations)

    def sample_distances(self, problem):
        distances = np.zeros(len(problem["modes"]))
        for i, purpose in enumerate(problem["purposes"]):
            cdf = self._CDFS.get(purpose, self._CDFS["other"])
            u = self.random.random_sample()
            distances[i] = np.interp(u, cdf, self._DISTANCES_M)
        return distances
```

**Step 2: Verify import works**

```bash
cd matsim_scenarios/bavaria && python -c "from synthesis.population.spatial.secondary.components import MiDDistanceSampler; print('OK')"
```

Expected: `OK`

**Step 3: Commit**

```bash
git add matsim_scenarios/bavaria/synthesis/population/spatial/secondary/components.py
git commit -m "feat: add MiDDistanceSampler for MiD 2017 Bayern distance CDFs"
```

---

### Task 2: Add `query_ring()` to CandidateIndex

**Files:**
- Modify: `matsim_scenarios/bavaria/synthesis/population/spatial/secondary/components.py`

**Step 1: Add query_ring method to CandidateIndex class (after line 65, after query_k)**

Insert after `query_k` and before `sample`:

```python
    def query_ring(self, purpose, center, target_distance, tolerance=0.3, max_candidates=20):
        """Find facilities in a distance ring [target*(1-tol), target*(1+tol)] from center.

        Returns list of (identifier, location) tuples sorted by distance-to-target error.
        Returns empty list if no facilities found in the ring.
        """
        outer_radius = target_distance * (1 + tolerance)
        inner_radius = max(0, target_distance * (1 - tolerance))

        indices = self.indices[purpose].query_radius(
            center.reshape(1, -1), outer_radius
        )[0]

        if len(indices) == 0:
            return []

        candidates = []
        for idx in indices:
            loc = self.data[purpose]["locations"][idx]
            dist = la.norm(loc - center)
            if dist >= inner_radius:
                candidates.append((
                    self.data[purpose]["identifiers"][idx],
                    loc,
                    abs(dist - target_distance)
                ))

        candidates.sort(key=lambda c: c[2])
        return [(c[0], c[1]) for c in candidates[:max_candidates]]
```

**Step 2: Verify import works**

```bash
cd matsim_scenarios/bavaria && python -c "from synthesis.population.spatial.secondary.components import CandidateIndex; print('OK')"
```

Expected: `OK`

**Step 3: Commit**

```bash
git add matsim_scenarios/bavaria/synthesis/population/spatial/secondary/components.py
git commit -m "feat: add ring-based facility search to CandidateIndex"
```

---

### Task 3: Update CustomDiscretizationSolver with ring query support

**Files:**
- Modify: `matsim_scenarios/bavaria/synthesis/population/spatial/secondary/components.py:73-116`

**Step 1: Replace the entire CustomDiscretizationSolver class (lines 73-116)**

```python
class CustomDiscretizationSolver(rda.DiscretizationSolver):
    def __init__(self, index, k_candidates = 1, use_ring_query = False):
        self.index = index
        self.k_candidates = k_candidates
        self.use_ring_query = use_ring_query

    def _ring_query_with_fallback(self, purpose, anchor, target_dist, relaxed_location):
        """Ring query with progressive tolerance widening, fallback to K-nearest."""
        for tolerance in [0.3, 0.6, 1.0]:
            candidates = self.index.query_ring(purpose, anchor, target_dist, tolerance=tolerance)
            if candidates:
                return candidates
        # Final fallback: K-nearest from relaxed location
        return self.index.query_k(purpose, relaxed_location, k=max(self.k_candidates, 10))

    def solve(self, problem, locations, target_distances = None):
        discretized_locations = []
        discretized_identifiers = []

        prev_anchor = None
        if problem["origin"] is not None:
            prev_anchor = problem["origin"].flatten()

        for i, (location, purpose) in enumerate(zip(locations, problem["purposes"])):
            has_target = (prev_anchor is not None and target_distances is not None
                          and i < len(target_distances))

            if has_target and self.use_ring_query:
                # Ring query: search facilities at target distance from anchor
                candidates = self._ring_query_with_fallback(
                    purpose, prev_anchor, target_distances[i], location
                )
                # Among ring candidates, prefer closest to relaxed location (chain direction)
                best_ident, best_loc = min(candidates, key=lambda c: la.norm(c[1] - location))

            elif has_target and self.k_candidates > 1:
                # Legacy: K-nearest from relaxed location
                candidates = self.index.query_k(purpose, location, k=self.k_candidates)
                target_dist = target_distances[i]
                best_error = np.inf
                best_ident, best_loc = candidates[0]
                for ident, loc in candidates:
                    error = abs(la.norm(loc - prev_anchor) - target_dist)
                    if error < best_error:
                        best_error = error
                        best_ident, best_loc = ident, loc
            else:
                best_ident, best_loc = self.index.query(purpose, location.reshape(1, -1))

            discretized_identifiers.append(best_ident)
            discretized_locations.append(best_loc)
            prev_anchor = best_loc

        assert len(discretized_locations) == problem["size"]

        return dict(
            valid = True, locations = np.vstack(discretized_locations),
            identifiers = discretized_identifiers
        )
```

**Step 2: Verify import works**

```bash
cd matsim_scenarios/bavaria && python -c "from synthesis.population.spatial.secondary.components import CustomDiscretizationSolver; print('OK')"
```

Expected: `OK`

**Step 3: Commit**

```bash
git add matsim_scenarios/bavaria/synthesis/population/spatial/secondary/components.py
git commit -m "feat: ring-based facility search in CustomDiscretizationSolver"
```

---

### Task 4: Wire up MiD mode in secondary locations.py

**Files:**
- Modify: `matsim_scenarios/bavaria/synthesis/population/spatial/secondary/locations.py`

**Step 1: Add config declaration in configure() (after line 28)**

Insert after `context.config("secloc_k_candidates", 5)`:

```python
    context.config("use_mid_distances", False)
```

**Step 2: Update import line 87 to include MiDDistanceSampler**

Replace line 87:

```python
from synthesis.population.spatial.secondary.components import CustomDistanceSampler, CustomDiscretizationSolver, CandidateIndex, CustomFreeChainSolver, MiDDistanceSampler
```

**Step 3: Update execute() to conditionally skip ENTD resampling (lines 89-102)**

Replace lines 89-102:

```python
def execute(context):
    # Load trips and primary locations
    df_trips = context.stage("synthesis.population.trips").sort_values(by = ["person_id", "trip_index"])
    df_trips["travel_time"] = df_trips["arrival_time"] - df_trips["departure_time"]
    df_primary, crs = prepare_locations(context)

    # Prepare data
    use_mid = context.config("use_mid_distances")

    if use_mid:
        distance_distributions = None  # Not needed — MiDDistanceSampler has built-in CDFs
    else:
        distance_distributions = context.stage("synthesis.population.spatial.secondary.distance_distributions")
        resample_distributions(distance_distributions, dict(
            car = 0.0, car_passenger = 0.1, pt = 0.5, bicycle = 0.0, walk = -0.5
        ))

    destinations = prepare_destinations(context)
```

**Step 4: Update process() to switch samplers (lines 143-168)**

Replace the sampler and solver setup block in `process()` (lines 148-168):

```python
  # Set up RNG
  random = np.random.RandomState(random_seed)
  maximum_iterations = context.config("secloc_maximum_iterations")
  use_mid = context.config("use_mid_distances")

  # Set up discretization solver
  destinations = context.data("destinations")
  candidate_index = CandidateIndex(destinations)
  k_candidates = context.config("secloc_k_candidates")
  discretization_solver = CustomDiscretizationSolver(
      candidate_index, k_candidates=k_candidates, use_ring_query=use_mid
  )

  # Set up distance sampler
  if use_mid:
      distance_sampler = MiDDistanceSampler(
          random=random, maximum_iterations=min(1000, maximum_iterations)
      )
  else:
      distance_distributions = context.data("distance_distributions")
      leisure_correction_factor = context.config("leisure_correction_factor")
      shop_correction_factor = context.config("shop_correction_factor")
      other_correction_factor = context.config("other_correction_factor")

      distance_sampler = CustomDistanceSampler(
          maximum_iterations=min(1000, maximum_iterations),
          random=random,
          distributions=distance_distributions,
          leisure_correction_factor=leisure_correction_factor,
          shop_correction_factor=shop_correction_factor,
          other_correction_factor=other_correction_factor)
```

**Step 5: Verify import works**

```bash
cd matsim_scenarios/bavaria && python -c "from synthesis.population.spatial.secondary import locations; print('OK')"
```

Expected: `OK`

**Step 6: Commit**

```bash
git add matsim_scenarios/bavaria/synthesis/population/spatial/secondary/locations.py
git commit -m "feat: wire MiD distance sampler + ring query in secondary locations"
```

---

### Task 5: Update commute_distance.py for primary MiD distances

**Files:**
- Modify: `matsim_scenarios/bavaria/synthesis/population/spatial/commute_distance.py`

**Step 1: Replace the entire file contents**

```python
import numpy as np
import pandas as pd
import logging

logger = logging.getLogger(__name__)

# MiD 2017 Bayern commute distance CDFs (euclidean meters)
# Source: MiD 2017 Kurzreport Bayern, routed km / 1.3 * 1000
_MID_DISTANCES_M = np.array([0, 769, 1538, 3846, 7692, 15385, 38462])

_MID_COMMUTE_CDFS = {
    "work":      np.array([0, .05, .13, .32, .53, .76, .95]),
    "education": np.array([0, .22, .38, .58, .77, .93, .99]),
}

def configure(context):
    context.stage("synthesis.population.enriched")
    context.stage("data.hts.commute_distance")

    context.config("commute_distance_scale_work", 1.0)
    context.config("commute_distance_scale_education", 1.0)
    context.config("use_mid_distances", False)

def execute(context):
    df_matching = context.stage("synthesis.population.enriched")
    use_mid = context.config("use_mid_distances")

    if use_mid:
        return _execute_mid(context, df_matching)
    else:
        return _execute_entd(context, df_matching)

def _execute_mid(context, df_matching):
    """Resample commute distances from MiD 2017 Bayern CDFs."""
    rng = np.random.RandomState(1234)
    n = len(df_matching)
    person_ids = df_matching["person_id"].values

    work_distances = np.interp(
        rng.random(n), _MID_COMMUTE_CDFS["work"], _MID_DISTANCES_M
    )
    edu_distances = np.interp(
        rng.random(n), _MID_COMMUTE_CDFS["education"], _MID_DISTANCES_M
    )

    # Apply any additional scaling (usually 1.0 with MiD)
    scale_work = context.config("commute_distance_scale_work")
    scale_education = context.config("commute_distance_scale_education")

    if scale_work != 1.0:
        logger.info("Scaling MiD work distances by %.2f", scale_work)
        work_distances *= scale_work
    if scale_education != 1.0:
        logger.info("Scaling MiD education distances by %.2f", scale_education)
        edu_distances *= scale_education

    df_work = pd.DataFrame({
        "person_id": person_ids,
        "commute_distance": work_distances,
    })
    df_education = pd.DataFrame({
        "person_id": person_ids,
        "commute_distance": edu_distances,
    })

    logger.info("MiD work commute: mean=%.0fm, median=%.0fm, p90=%.0fm",
                work_distances.mean(), np.median(work_distances), np.percentile(work_distances, 90))
    logger.info("MiD education commute: mean=%.0fm, median=%.0fm, p90=%.0fm",
                edu_distances.mean(), np.median(edu_distances), np.percentile(edu_distances, 90))

    assert len(df_work) == len(df_matching)
    assert len(df_education) == len(df_matching)

    return dict(work=df_work, education=df_education)

def _execute_entd(context, df_matching):
    """Legacy: use ENTD HTS commute distances with scaling factors."""
    df_commute_distance = context.stage("data.hts.commute_distance")

    scale_work = context.config("commute_distance_scale_work")
    scale_education = context.config("commute_distance_scale_education")

    df_work = pd.merge(
        df_matching[["person_id", "hts_id"]],
        df_commute_distance["work"][["person_id", "commute_distance"]].rename(columns = dict(person_id = "hts_id")),
        how = "left"
    )

    df_education = pd.merge(
        df_matching[["person_id", "hts_id"]],
        df_commute_distance["education"][["person_id", "commute_distance"]].rename(columns = dict(person_id = "hts_id")),
        how = "left"
    )

    if scale_work != 1.0:
        logger.info("Scaling work commute distances by factor %.2f", scale_work)
        df_work["commute_distance"] = df_work["commute_distance"] * scale_work

    if scale_education != 1.0:
        logger.info("Scaling education commute distances by factor %.2f", scale_education)
        df_education["commute_distance"] = df_education["commute_distance"] * scale_education

    assert len(df_work) == len(df_matching)
    assert len(df_education) == len(df_matching)

    return dict(work=df_work, education=df_education)
```

**Step 2: Verify import works**

```bash
cd matsim_scenarios/bavaria && python -c "from synthesis.population.spatial.commute_distance import configure, execute; print('OK')"
```

Expected: `OK`

**Step 3: Commit**

```bash
git add matsim_scenarios/bavaria/synthesis/population/spatial/commute_distance.py
git commit -m "feat: MiD 2017 Bayern commute distance CDFs for work/education"
```

---

### Task 6: Update pipeline configs

**Files:**
- Modify: `matsim_scenarios/bavaria/config_kelheim_30km_100pct.yml`
- Modify: `matsim_scenarios/bavaria/config_kelheim_30km_10pct.yml`
- Modify: `matsim_scenarios/bavaria/config_kelheim_30km_1pct.yml`

**Step 1: Add `use_mid_distances: true` to all three configs**

In each file, add after the `secloc_k_candidates: 5` line:

```yaml
  # Use MiD 2017 Bayern distance CDFs instead of ENTD French data
  use_mid_distances: true
```

Also reset the commute distance scales to 1.0 (MiD CDFs are already calibrated):

```yaml
  commute_distance_scale_work: 1.0
  commute_distance_scale_education: 1.0
```

And reset purpose correction factors to 1.0 (not used with MiD but kept for documentation):

```yaml
  shop_correction_factor: 1.0
  leisure_correction_factor: 1.0
  other_correction_factor: 1.0
```

**Step 2: Commit**

```bash
git add matsim_scenarios/bavaria/config_kelheim_30km_*.yml
git commit -m "feat: enable MiD distance calibration in all Bavaria configs"
```

---

### Task 7: Smoke test with 1% pipeline

**Step 1: Run the 1% pipeline**

```bash
cd matsim_scenarios/bavaria
python -m synpp config_kelheim_30km_1pct.yml
```

Expected: Pipeline completes without errors. Look for log output:
- `MiD work commute: mean=~7700m, median=~5500m`
- `MiD education commute: mean=~3500m, median=~2200m`
- `Success rate:` for secondary locations (should be >0.8)

**Step 2: Quick distance check on output**

```bash
cd matsim_scenarios/bavaria && python -c "
import gzip, xml.etree.ElementTree as ET, numpy as np

POP = 'output/kelheim_30km_1pct/kelheim_30km_1pct_population.xml.gz'
INTERACTION = {'car interaction', 'pt interaction', 'ride interaction', 'freight interaction'}

dists = []
with gzip.open(POP, 'rb') as f:
    for ev, elem in ET.iterparse(f, events=('end',)):
        if elem.tag == 'person':
            plan = elem.find(\"plan[@selected='yes']\")
            if plan is None:
                elem.clear(); continue
            prev_xy = None
            for ch in plan:
                if ch.tag == 'activity' and ch.get('type') not in INTERACTION:
                    xy = (float(ch.get('x',0)), float(ch.get('y',0)))
                    if prev_xy is not None:
                        d = np.sqrt((xy[0]-prev_xy[0])**2 + (xy[1]-prev_xy[1])**2)
                        if d > 0:
                            dists.append(d/1000)
                    prev_xy = xy
            elem.clear()

dists = np.array(dists)
print(f'Trips: {len(dists):,}')
print(f'Mean: {dists.mean():.1f} km, Median: {np.median(dists):.1f} km')
for t in [1, 2, 5, 10, 20, 50]:
    print(f'  <{t}km: {(dists < t/1.3).mean()*100:.1f}%')
"
```

Expected: Distance distribution should be closer to MiD targets:
- `<1km (routed)`: ~15-25% (was 20-35%)
- `<5km`: ~45-60% (was 60-75%)
- Mean: ~8-12km (was 5-9km)

**Step 3: Commit smoke test results / notes**

If results look good, no additional commit needed. If adjustments needed, document findings.

---

### Task 8: Run comparison notebook on new 1% output

**Step 1: Parse the new 1% population in the comparison notebook**

Update `population_comparison.ipynb` cell 4 to point to the new 1% output:

```python
POP_XML = BASE / "bavaria/output/kelheim_30km_1pct/kelheim_30km_1pct_population.xml.gz"
```

**Step 2: Execute notebook and compare distance distributions**

```bash
cd matsim_scenarios/notebooks
python -m jupyter nbconvert --to notebook --execute --ExecutePreprocessor.timeout=600 --ExecutePreprocessor.allow_errors=True --output population_comparison_executed.ipynb population_comparison.ipynb
```

**Step 3: Review distance band table vs MiD targets**

Key metrics to check:
- Work 0-1km should drop from 20.6% toward 5%
- Shop 0-1km should drop from 30.2% toward 25%
- Leisure 0-1km should drop from 33.1% toward 18%
- Education 0-1km should drop from 35.9% toward 22%

**Step 4: Commit updated notebook**

```bash
git add matsim_scenarios/notebooks/population_comparison.ipynb
git commit -m "feat: compare MiD-calibrated population against Kelheim + MiD targets"
```
