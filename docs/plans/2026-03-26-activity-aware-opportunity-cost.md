# Activity-Aware Opportunity Cost Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace constant-rate opportunity cost with activity-aware logarithmic opportunity cost that preserves MATSim's log-utility scoring function, with a config option to choose between LINEAR (current) and LOG (new) models.

**Architecture:** Add `OpportunityCostModel` enum (NONE, LINEAR, LOG) to `ExMasConfigGroup`, replacing the boolean `includeOpportunityCost`. Extract a shared `OpportunityCostCalculator` utility class that computes per-trip opportunity cost using either the constant rate or the exact log-utility formula `min(beta_perf * t_typ_A * ln(t_A / (t_A - tt)), ...)`. Both `ModeRoutingCache` and `DrtTripScorer` delegate to this calculator.

**Tech Stack:** Java 17, MATSim 2026.0-SNAPSHOT, Maven

---

## Context

### Current behavior
- `ModeRoutingCache.scoreViaAdapter()` and `DrtTripScorer.score()` both apply:
  ```java
  score -= totalTravelTime * params.marginalUtilityOfPerforming_s;
  ```
- Guarded by `exMasConfig.isIncludeOpportunityCost() && !adapter.includesOpportunityCost()`
- Eqasim adapter returns `includesOpportunityCost() = true` → unaffected

### Activity params availability
- **Kelheim:** Uses `Activities.addScoringParams()` which creates activity types like `home_28800`, `work_28800` with `setTypicalDuration(ii)`. Always available in `ScoringParameters.utilParams`.
- **Bavaria (eqasim):** Uses eqasim adapter → opportunity cost already included → this code path is skipped entirely.
- **Tests:** `ExMasClusteredHyperPoolE2ETest` sets `typicalDuration` for home (12h) and work (8h).

### Files that reference opportunity cost
- `ExMasConfigGroup.java` — config field + getter/setter + comments map
- `ModeRoutingCache.java` — applies in `scoreViaAdapter()`
- `DrtTripScorer.java` — applies in `score()`
- `RunKelheimDemandExtraction.java` — sets `setIncludeOpportunityCost(true)`
- `RunBavaria30kmDemandExtraction.java` — sets `setIncludeOpportunityCost(true)` + logging
- `RunScoringAdapterValidation.java` — sets true/false in various places
- `DemandExtractionScoringAdapter.java` — javadoc references
- `BinarySearchConvergenceTest.java` — sets false
- `PlanCalcScoreAdapterTest.java` — asserts `includesOpportunityCost() == false`

### DrtTripScorer callers
- `BudgetToConstraintsCalculator.scoreDrtTrip()` — has `Population` injected
- `BudgetValidator.calculateDrtScore()` — has `Population` injected
- Both can access `person.getSelectedPlan()` for activity durations

---

## Task 1: Add OpportunityCostModel enum and replace boolean in ExMasConfigGroup

**Files:**
- Modify: `src/main/java/org/matsim/contrib/demand_extraction/config/ExMasConfigGroup.java`

**Step 1: Add the enum and new field**

Add inside `ExMasConfigGroup`, after `TourEvaluationMode`:

```java
public enum OpportunityCostModel {
    /** No opportunity cost added to trip scoring. */
    NONE,
    /** Constant rate: marginalUtilityOfPerforming_s * travelTime (MATSim default approximation). */
    LINEAR,
    /** Exact log-utility: min over origin/dest of beta_perf * t_typ * ln(t_actual / (t_actual - tt)).
     *  Falls back to LINEAR for activities without typicalDuration. */
    LOG
}
```

Replace the field:
```java
// OLD:
private boolean includeOpportunityCost = true;

// NEW:
private OpportunityCostModel opportunityCostModel = OpportunityCostModel.LINEAR;
```

**Step 2: Replace getter/setter**

Replace the old `@StringGetter("includeOpportunityCost")` / `@StringSetter("includeOpportunityCost")` block with:

```java
@StringGetter("opportunityCostModel")
public OpportunityCostModel getOpportunityCostModel() {
    return opportunityCostModel;
}

@StringSetter("opportunityCostModel")
public void setOpportunityCostModel(OpportunityCostModel opportunityCostModel) {
    this.opportunityCostModel = opportunityCostModel;
}
```

Keep backward-compat helper (deprecate the old boolean methods but keep them working):

```java
/** @deprecated Use {@link #setOpportunityCostModel} instead. */
@Deprecated
public void setIncludeOpportunityCost(boolean include) {
    this.opportunityCostModel = include ? OpportunityCostModel.LINEAR : OpportunityCostModel.NONE;
}

/** @deprecated Use {@link #getOpportunityCostModel} instead. */
@Deprecated
public boolean isIncludeOpportunityCost() {
    return opportunityCostModel != OpportunityCostModel.NONE;
}
```

Remove the `@StringGetter`/`@StringSetter` annotations from the deprecated methods (they now live on the new enum methods).

**Step 3: Update comments map**

Replace the `includeOpportunityCost` entry:

```java
map.put("opportunityCostModel",
    "Opportunity cost model for trip scoring. NONE = no opportunity cost, " +
    "LINEAR = constant marginalUtilityOfPerforming_s * travelTime (MATSim default), " +
    "LOG = exact log-utility with activity-aware durations " +
    "(min of origin/dest: beta_perf * t_typ * ln(t_actual / (t_actual - tt))). " +
    "Falls back to LINEAR for activities without typicalDuration. Default: LINEAR");
```

Remove the old `includeOpportunityCost` entry from the map.

**Step 4: Compile**

Run: `mvn compile -q -Denforcer.skip=true`
Expected: SUCCESS (there will be compile errors in callers — that's expected, fixed in later tasks)

**Step 5: Commit**

```
feat: add OpportunityCostModel enum (NONE/LINEAR/LOG) to ExMasConfigGroup
```

---

## Task 2: Create OpportunityCostCalculator utility class

**Files:**
- Create: `src/main/java/org/matsim/contrib/demand_extraction/scoring/OpportunityCostCalculator.java`

**Step 1: Write the utility class**

```java
package org.matsim.contrib.demand_extraction.scoring;

import org.matsim.api.core.v01.population.Activity;
import org.matsim.api.core.v01.population.Leg;
import org.matsim.api.core.v01.population.Plan;
import org.matsim.api.core.v01.population.PlanElement;
import org.matsim.contrib.demand_extraction.config.ExMasConfigGroup.OpportunityCostModel;
import org.matsim.core.scoring.functions.ActivityUtilityParameters;
import org.matsim.core.scoring.functions.ScoringParameters;

/**
 * Computes opportunity cost for trips using either a constant rate (LINEAR)
 * or MATSim's exact log-utility formula (LOG).
 *
 * <p>LOG model: the rational traveler shortens whichever adjacent activity has
 * the lower marginal utility of time. The exact utility loss from shortening
 * activity A by delta seconds is:
 * <pre>
 *   loss = beta_perf * t_typ * ln(t_actual / (t_actual - delta))
 * </pre>
 * The opportunity cost is the minimum of this over origin and destination.
 */
public final class OpportunityCostCalculator {

    private OpportunityCostCalculator() {}

    /**
     * Compute opportunity cost (positive value to subtract from score).
     *
     * @param model           the opportunity cost model (LINEAR or LOG)
     * @param params          scoring parameters for the person
     * @param travelTime      total travel time of the trip (seconds)
     * @param originActivity  the origin activity (for LOG: type lookup)
     * @param destActivity    the destination activity (for LOG: type lookup)
     * @param originDuration  actual duration of origin activity in seconds (for LOG)
     * @param destDuration    actual duration of destination activity in seconds (for LOG)
     * @return opportunity cost in utils (always >= 0)
     */
    public static double compute(OpportunityCostModel model, ScoringParameters params,
            double travelTime, Activity originActivity, Activity destActivity,
            double originDuration, double destDuration) {

        if (model == OpportunityCostModel.NONE || travelTime <= 0) {
            return 0.0;
        }

        if (model == OpportunityCostModel.LINEAR) {
            return travelTime * params.marginalUtilityOfPerforming_s;
        }

        // LOG model: exact log-utility with activity-aware durations
        double betaPerf = params.marginalUtilityOfPerforming_s;
        double originLoss = logUtilityLoss(betaPerf, params, originActivity, originDuration, travelTime);
        double destLoss = logUtilityLoss(betaPerf, params, destActivity, destDuration, travelTime);

        return Math.min(originLoss, destLoss);
    }

    /**
     * Compute the exact log-utility loss from shortening an activity by delta seconds.
     *
     * <p>Formula: beta_perf * t_typ * ln(t_actual / (t_actual - delta))
     *
     * <p>Falls back to linear (beta_perf * delta) if activity params are missing
     * or typicalDuration is not set.
     */
    private static double logUtilityLoss(double betaPerf, ScoringParameters params,
            Activity activity, double actualDuration, double delta) {

        if (actualDuration <= delta) {
            // Can't shorten below 0 — this activity can't absorb the travel time
            return Double.MAX_VALUE;
        }

        ActivityUtilityParameters actParams = params.utilParams.get(activity.getType());
        if (actParams == null || actParams.typicalDuration_s <= 0 || !actParams.scoreAtAll) {
            // Fallback to linear
            return betaPerf * delta;
        }

        double tTyp = actParams.typicalDuration_s;
        return betaPerf * tTyp * Math.log(actualDuration / (actualDuration - delta));
    }

    /**
     * Compute actual activity durations from a person's selected plan.
     *
     * <p>Walks through plan elements with a clock. First activity starts at t=0,
     * last activity ends at t=86400 (end of day).
     *
     * @param plan the person's selected plan
     * @return array of activity durations in seconds, indexed by activity position
     *         (activity 0 = first, activity N = last). Trip i has origin = index i,
     *         destination = index i+1.
     */
    public static double[] computeActivityDurations(Plan plan) {
        // Count activities
        int numActivities = 0;
        for (PlanElement pe : plan.getPlanElements()) {
            if (pe instanceof Activity) numActivities++;
        }

        double[] durations = new double[numActivities];
        double clock = 0.0;
        int actIdx = 0;

        for (PlanElement pe : plan.getPlanElements()) {
            if (pe instanceof Activity act) {
                double startTime = clock;
                double endTime;
                if (act.getEndTime().isPresent()) {
                    endTime = act.getEndTime().getAsDouble();
                } else {
                    endTime = 86400.0; // last activity: assume end of day
                }
                durations[actIdx] = Math.max(0.0, endTime - startTime);
                clock = endTime;
                actIdx++;
            } else if (pe instanceof Leg leg) {
                clock += leg.getTravelTime().orElse(0.0);
            }
        }

        return durations;
    }
}
```

**Step 2: Compile**

Run: `mvn compile -q -Denforcer.skip=true`
Expected: SUCCESS (new class, no callers yet)

**Step 3: Commit**

```
feat: add OpportunityCostCalculator with LINEAR and LOG models
```

---

## Task 3: Update ModeRoutingCache to use OpportunityCostCalculator

**Files:**
- Modify: `src/main/java/org/matsim/contrib/demand_extraction/demand/ModeRoutingCache.java`

**Step 1: Import new classes**

Add imports:
```java
import org.matsim.contrib.demand_extraction.config.ExMasConfigGroup.OpportunityCostModel;
import org.matsim.contrib.demand_extraction.scoring.OpportunityCostCalculator;
```

**Step 2: Pre-compute activity durations in cacheModes()**

After the existing `totalDailyDistance_m` computation (added in the daily constant task), add:

```java
// Compute activity durations for LOG opportunity cost model
double[] activityDurations = null;
if (exMasConfig.getOpportunityCostModel() == OpportunityCostModel.LOG
        && !adapter.includesOpportunityCost()) {
    activityDurations = OpportunityCostCalculator.computeActivityDurations(person.getSelectedPlan());
}
```

**Step 3: Pass activity durations to scoreViaAdapter()**

Update the call (already has `distance` and `totalDailyDistance_m` from the daily constant task):

```java
double score = scoreViaAdapter(person, mode, tripElements, trip,
        tripIndex, params, previousTrips, distance, totalDailyDistance_m,
        activityDurations);
```

**Step 4: Update scoreViaAdapter() signature and replace opportunity cost block**

Update signature to add `double[] activityDurations` parameter.

Replace the opportunity cost block:

```java
// OLD:
if (exMasConfig.isIncludeOpportunityCost() && !adapter.includesOpportunityCost()) {
    double totalTravelTime = 0.0;
    for (PlanElement pe : tripElements) {
        if (pe instanceof Leg leg) {
            totalTravelTime += leg.getTravelTime().orElse(0.0);
        }
    }
    score -= totalTravelTime * params.marginalUtilityOfPerforming_s;
}

// NEW:
OpportunityCostModel oppCostModel = exMasConfig.getOpportunityCostModel();
if (oppCostModel != OpportunityCostModel.NONE && !adapter.includesOpportunityCost()) {
    double totalTravelTime = 0.0;
    for (PlanElement pe : tripElements) {
        if (pe instanceof Leg leg) {
            totalTravelTime += leg.getTravelTime().orElse(0.0);
        }
    }
    double originDuration = (activityDurations != null && tripIndex < activityDurations.length)
            ? activityDurations[tripIndex] : 0.0;
    double destDuration = (activityDurations != null && tripIndex + 1 < activityDurations.length)
            ? activityDurations[tripIndex + 1] : 0.0;
    score -= OpportunityCostCalculator.compute(oppCostModel, params,
            totalTravelTime, trip.getOriginActivity(), trip.getDestinationActivity(),
            originDuration, destDuration);
}
```

**Step 5: Compile**

Run: `mvn compile -q -Denforcer.skip=true`
Expected: Compile errors in DrtTripScorer (still uses old API) — fix in next task

**Step 6: Commit**

```
feat: use OpportunityCostCalculator in ModeRoutingCache
```

---

## Task 4: Update DrtTripScorer to use OpportunityCostCalculator

**Files:**
- Modify: `src/main/java/org/matsim/contrib/demand_extraction/scoring/DrtTripScorer.java`

**Step 1: Add opportunityCostOverride parameter to score()**

The idea: callers pre-compute the opportunity cost for the trip's origin/destination activities, and pass it as a value. DrtTripScorer just subtracts it (scaled by its own travel time). This avoids DrtTripScorer needing to know about activity durations.

Actually, simpler approach: add the full set of parameters needed. Update the `score()` signature to add:

```java
OpportunityCostModel opportunityCostModel,
Activity originActivity,      // the REAL origin activity (not synthetic)
Activity destinationActivity, // the REAL destination activity (not synthetic)
double originDuration,
double destDuration
```

Replace the opportunity cost block:

```java
// OLD:
if (exMasConfig.isIncludeOpportunityCost() && !adapter.includesOpportunityCost()) {
    double totalTravelTime = accessTime + travelTime + egressTime;
    ScoringParameters scoringParams = scoringParametersForPerson.getScoringParameters(person);
    score -= totalTravelTime * scoringParams.marginalUtilityOfPerforming_s;
}

// NEW:
if (opportunityCostModel != OpportunityCostModel.NONE && !adapter.includesOpportunityCost()) {
    double totalTravelTime = accessTime + travelTime + egressTime;
    ScoringParameters scoringParams = scoringParametersForPerson.getScoringParameters(person);
    score -= OpportunityCostCalculator.compute(opportunityCostModel, scoringParams,
            totalTravelTime, originActivity, destinationActivity,
            originDuration, destDuration);
}
```

Remove the `exMasConfig` parameter from `score()` — it's only used for `getDrtMode()` and `isIncludeOpportunityCost()`. Replace with `String drtMode` and `OpportunityCostModel opportunityCostModel` to keep the signature clean.

**Step 2: Update BudgetToConstraintsCalculator.scoreDrtTrip()**

The caller needs to look up the person's plan, compute activity durations, and pass origin/dest activity + durations. The `DrtRequest` has `tripIndex` which maps to the plan's trip list.

```java
private double scoreDrtTrip(Person person, DrtRequest request, ...) {
    // Look up real activities from the person's plan
    List<TripStructureUtils.Trip> trips = TripStructureUtils.getTrips(person.getSelectedPlan());
    Activity originActivity;
    Activity destActivity;
    double originDuration = 0.0;
    double destDuration = 0.0;

    if (request.tripIndex >= 0 && request.tripIndex < trips.size()) {
        TripStructureUtils.Trip trip = trips.get(request.tripIndex);
        originActivity = trip.getOriginActivity();
        destActivity = trip.getDestinationActivity();

        double[] actDurations = OpportunityCostCalculator.computeActivityDurations(person.getSelectedPlan());
        originDuration = (request.tripIndex < actDurations.length) ? actDurations[request.tripIndex] : 0.0;
        destDuration = (request.tripIndex + 1 < actDurations.length) ? actDurations[request.tripIndex + 1] : 0.0;
    } else {
        // Fallback: synthetic activities (existing behavior)
        originActivity = PopulationUtils.createActivityFromLinkId("unknown", request.originLinkId);
        destActivity = PopulationUtils.createActivityFromLinkId("unknown", request.destinationLinkId);
    }

    return DrtTripScorer.score(person, request, adapter, scoringParametersForPerson,
            exMasConfig.getDrtMode(), exMasConfig.getOpportunityCostModel(),
            travelTime, distance, accessWalkDist, egressWalkDist, delay, walkSpeed,
            originActivity, destActivity, originDuration, destDuration);
}
```

Note: `computeActivityDurations` is called per DRT scoring call here, which is in a tight binary search loop. For performance, pre-compute once per person and cache. But for now keep it simple — the plan iteration is O(number of plan elements) which is small. Optimize later if profiling shows it matters.

**Step 3: Update BudgetValidator.calculateDrtScore()**

Same pattern as BudgetToConstraintsCalculator:

```java
private double calculateDrtScore(DrtRequest request, ...) {
    Person person = population.getPersons().get(request.personId);
    List<TripStructureUtils.Trip> trips = TripStructureUtils.getTrips(person.getSelectedPlan());
    Activity originActivity;
    Activity destActivity;
    double originDuration = 0.0;
    double destDuration = 0.0;

    if (request.tripIndex >= 0 && request.tripIndex < trips.size()) {
        TripStructureUtils.Trip trip = trips.get(request.tripIndex);
        originActivity = trip.getOriginActivity();
        destActivity = trip.getDestinationActivity();
        double[] actDurations = OpportunityCostCalculator.computeActivityDurations(person.getSelectedPlan());
        originDuration = (request.tripIndex < actDurations.length) ? actDurations[request.tripIndex] : 0.0;
        destDuration = (request.tripIndex + 1 < actDurations.length) ? actDurations[request.tripIndex + 1] : 0.0;
    } else {
        originActivity = PopulationUtils.createActivityFromLinkId("unknown", request.originLinkId);
        destActivity = PopulationUtils.createActivityFromLinkId("unknown", request.destinationLinkId);
    }

    return DrtTripScorer.score(person, request, adapter, scoringParametersForPerson,
            exMasConfig.getDrtMode(), exMasConfig.getOpportunityCostModel(),
            actualTravelTime, actualDistance,
            actualWalkDistanceAccess, actualWalkDistanceEgress, delay, walkSpeed,
            originActivity, destActivity, originDuration, destDuration);
}
```

**Step 4: Compile**

Run: `mvn compile -q -Denforcer.skip=true`
Expected: SUCCESS

**Step 5: Commit**

```
feat: use OpportunityCostCalculator in DrtTripScorer and callers
```

---

## Task 5: Update run files and validation tool

**Files:**
- Modify: `src/main/java/org/matsim/contrib/demand_extraction/run/RunKelheimDemandExtraction.java`
- Modify: `src/main/java/org/matsim/contrib/demand_extraction/run/RunBavaria30kmDemandExtraction.java`
- Modify: `src/main/java/org/matsim/contrib/demand_extraction/run/RunScoringAdapterValidation.java`

**Step 1: Update RunKelheimDemandExtraction**

Replace:
```java
exMasConfig.setIncludeOpportunityCost(true);
```
with:
```java
exMasConfig.setOpportunityCostModel(ExMasConfigGroup.OpportunityCostModel.LOG);
```

Update the log line:
```java
log.info("  Opportunity cost model: {}", exMasConfig.getOpportunityCostModel());
```

**Step 2: Update RunBavaria30kmDemandExtraction**

Same changes as Kelheim. (Eqasim adapter will skip opportunity cost anyway via `includesOpportunityCost() = true`.)

**Step 3: Update RunScoringAdapterValidation**

Replace all `setIncludeOpportunityCost(false)` calls with:
```java
exMasConfig.setOpportunityCostModel(ExMasConfigGroup.OpportunityCostModel.NONE);
```

Replace `setIncludeOpportunityCost(true)` calls with:
```java
exMasConfig.setOpportunityCostModel(ExMasConfigGroup.OpportunityCostModel.LOG);
```

**Step 4: Update adapter interface javadoc**

In `DemandExtractionScoringAdapter.java`, update the reference from `exMasConfig.isIncludeOpportunityCost()` to `exMasConfig.getOpportunityCostModel()`.

**Step 5: Compile**

Run: `mvn compile -q -Denforcer.skip=true`
Expected: SUCCESS

**Step 6: Commit**

```
feat: set LOG opportunity cost model in Kelheim and Bavaria run files
```

---

## Task 6: Run tests and fix any failures

**Files:**
- Modify: test files as needed

**Step 1: Run all tests**

Run: `mvn test -Denforcer.skip=true`
Expected: All 71 tests pass (the config change is backward-compatible via deprecated methods)

**Step 2: Fix any test failures**

Tests that call `setIncludeOpportunityCost(false)` use the deprecated method, which maps to `NONE`. These should still work. If any test explicitly checks config serialization for `includeOpportunityCost`, update to `opportunityCostModel`.

Check `BinarySearchConvergenceTest` (sets `setIncludeOpportunityCost(false)`) — should work via deprecated method.

**Step 3: Commit if fixes needed**

```
fix: update tests for OpportunityCostModel enum
```

---

## Task 7: Update logging to show opportunity cost details

**Files:**
- Modify: `src/main/java/org/matsim/contrib/demand_extraction/demand/ModeRoutingCache.java`

**Step 1: Add summary logging**

After mode caching completes (near the "Mode caching complete" log line), add a one-time log of the opportunity cost model:

```java
log.info("Opportunity cost model: {} (adapter includes OC: {})",
        exMasConfig.getOpportunityCostModel(), adapter.includesOpportunityCost());
```

**Step 2: Compile and test**

Run: `mvn test -Denforcer.skip=true`
Expected: All tests pass

**Step 3: Commit**

```
feat: log opportunity cost model in mode caching output
```
