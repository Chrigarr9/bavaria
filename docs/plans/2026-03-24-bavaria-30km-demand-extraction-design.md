# Design: Bavaria 30km DRT Demand Extraction Runner

**Date:** 2026-03-24
**Author:** Christoph Garritsen + Claude
**Status:** Approved

## Problem

Run DRT demand extraction on the Bavaria 30km eqasim population (upsampled to 25%/100%)
using calibrated Kelheim scoring parameters. The existing `RunKelheimDemandExtraction` is
tightly coupled to the Kelheim scenario (SVN URLs, KEXI-specific DRT config, Kelheim network
link IDs). A new runner is needed for the Bavaria infrastructure.

## Architecture

Single new Java class `RunBavaria30kmDemandExtraction.java` in the existing
`org.matsim.contrib.demand_extraction.run` package. Builds a MATSim `Config` programmatically
(not from an existing XML) because the Bavaria eqasim config uses incompatible modules
(DMC/eqasim utility estimators, zero marginalUtilityOfMoney, etc.).

### Data Flow

```
RunPopulationUpsampling (separate step)
  -> upsampled_population.xml.gz (0% base + X% donor)

RunBavaria30kmDemandExtraction
  -> loads Bavaria network, transit, facilities, vehicles from disc
  -> loads pre-upsampled population from --population arg
  -> applies Kelheim v3.0 calibrated scoring (hardcoded)
  -> enables income-dependent marginalUtilityOfMoney
  -> runs N iterations (warm-up) OR 0 iterations (restart/free-flow)
  -> DemandExtractionModule fires at shutdown
  -> outputs drt_requests.csv, exmas_rides.csv, etc.
```

## CLI Arguments

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--scenario-path` | yes | — | Path to `bavaria/output/kelheim_30km_100pct/` |
| `--population` | yes | — | Path to population XML (pre-upsampled or raw eqasim) |
| `--sample` | no | `1` | Sample percentage (1, 10, 25, 100) |
| `--iterations` | no | `0` | MATSim iterations (0 = free-flow travel times) |
| `--dmc-start-rate` | no | `0.2` | Initial SubtourModeChoice replanning rate |
| `--dmc-end-rate` | no | `0.05` | Final SubtourModeChoice replanning rate |
| `--output-dir` | no | auto | Output directory |
| `--deterministic` | no | false | Single-threaded for reproducibility |
| `--algorithm-process-count` | no | -1 | ExMAS parallelism |
| `--heuristics-process-count` | no | -1 | ExMAS heuristics parallelism |
| `--no-cleanup` | no | false | Keep all MATSim iteration output |

## Config Assembly (Programmatic)

### Input Files (from `--scenario-path`)

All resolved relative to `--scenario-path` using prefix `kelheim_30km_100pct_`:

- `network.xml.gz` — Bavaria 30km network (126k nodes, 287k links)
- `transit_schedule.xml.gz` — GTFS-derived transit
- `transit_vehicles.xml.gz`
- `vehicles.xml.gz`
- `facilities.xml.gz` — 1.3M facilities from OSM

Population loaded from `--population` argument (decoupled from scenario path).

### Kelheim v3.0 Calibrated Scoring (Hardcoded)

These values come from `kelheim-v3.0-25pct.kexi.config.xml` and were calibrated against
observed mode shares and KEXI ridership data. They MUST be used together with
income-dependent marginal utility of money (see below).

**Global scoring params:**
- `marginalUtilityOfMoney = 1.0` (config-level default; person-specific via income scaling)
- `performing = 6.0` utils/hr
- `waitingPt = -1.6`
- `lateArrival = -18.0`
- `utilityOfLineSwitch = -1.0`

**Mode params:**

| Mode | constant | margUtilTravel (utils/hr) | margUtilDist (utils/m) | monetaryDistRate (€/m) | dailyMonetary (€) |
|------|----------|--------------------------|------------------------|------------------------|-------------------|
| car | 0.109 | 0.0 | 0.0 | -2.0E-4 | -5.3 |
| ride | -0.449 | -12.0 | 0.0 | -2.0E-4 | 0.0 |
| pt | 0.045 | 0.0 | 0.0 | 0.0 | 0.0 |
| bike | -0.906 | -3.0 | 0.0 | 0.0 | 0.0 |
| walk | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| drt | 2.45 | 0.0 | -2.5E-4 | 0.0 | 0.0 |

**DRT scoring note:** The `margUtilDist=-2.5E-4` is the **non-monetary** distance disutility,
calibrated against real KEXI data. The actual KEXI fare (zone-based 2-3€) was handled by a
separate `KelheimDrtFareModule` (not loaded here). For demand extraction, `minDrtCostPerKm=0.0`
because fare optimization happens downstream in ExmasCommuters.

### Income-Dependent Marginal Utility of Money

**CRITICAL:** The Kelheim scoring params were calibrated WITH income-dependent scaling active.
The runner MUST bind `IncomeDependentUtilityOfMoneyPersonScoringParameters`:

```java
bind(ScoringParametersForPerson.class)
    .to(IncomeDependentUtilityOfMoneyPersonScoringParameters.class)
    .asEagerSingleton();
```

This computes per-person: `margUtilMoney_person = config_value × avgIncome / personalIncome`

The upsampled population has per-person income attributes (from `AttributeAdapter` in the
upsampling step), so this works out of the box. Low-income agents are more price-sensitive,
affecting both mode choice during warm-up iterations and the maxCost budget calculation.

### Activity Scoring Params

Bavaria eqasim uses simple activity names (no duration suffixes). Register with typical durations:

| Activity | Typical Duration | scoringThisActivityAtAll |
|----------|-----------------|--------------------------|
| home | 12h | true |
| work | 8h | true |
| education | 6h | true |
| shop | 1h | true |
| leisure | 2h | true |
| other | 2h | true |
| outside | — | false |
| freight_loading | — | false |
| freight_unloading | — | false |
| *interaction types* | — | false |

## Iteration Strategy

### When `--iterations 0` (restart / free-flow)

- `lastIteration = 0`
- No replanning strategies registered
- Travel times from iteration-0 free-flow speeds (or from loaded converged plans)
- Demand extraction fires at shutdown

### When `--iterations N` (warm-up)

- `lastIteration = N`
- Replanning strategies:
  - `ChangeExpBeta` weight=0.85 (plan selection)
  - `ReRoute` weight=0.10
  - `SubtourModeChoice` weight=variable (annealed from `dmc-start-rate` to `dmc-end-rate`)
  - `TimeAllocationMutator` weight=0.10, range=7200s
- `ReplanningAnnealer` sigmoid: `startValue=dmc-start-rate`, `endValue=dmc-end-rate`, `halfLife=0.5`
- `fractionOfIterationsToDisableInnovation = 0.9`
- `fractionOfIterationsToStartScoreMSA = 0.9`
- `flowCapacityFactor` / `storageCapacityFactor` = `sample / 100`
- `SubtourModeChoice`: modes=`car,pt,bike,walk`, chainBased=`car,bike`, considerCarAvailability=true
- Demand extraction fires at shutdown after all iterations

## DRT Setup

Minimal DRT configuration for demand extraction:

- Single DRT mode (`drt`) on car network
- No service area restriction (ExMAS budget filtering handles viability)
- No fleet, no stops, no operational scheme (DRT not simulated, only scored)
- `DrtControlerCreator.createScenarioWithDrtRouteFactory` for route factory
- `DemandExtractionConfigValidator.prepareConfigForDemandExtraction` handles DRT config defaults

## ExMAS Configuration

Identical to `RunKelheimDemandExtraction`:

- DRT mode: `drt`, routing mode: `car`
- Base modes: `car`, `pt`, `walk`, `bike`
- Private vehicle modes: `car`, `bike`
- Commute filter: `COMMUTES_AND_EDUCATION` (home ↔ work + education)
- Min age: 13
- Min DRT cost/km: 0.0 (fare varied downstream)
- Max pooling degree: 16
- Max detour factor: 1.5
- Heuristic pruning enabled (same settings)
- Predecessor calculation enabled
- Shapley values enabled
- Include opportunity cost: true
- PT departure optimization: disabled (SwissRailRaptor config issues)

## Population Filtering

Before demand extraction:

1. Remove agents with subpopulation `freight` or `truck`
2. Remove agents whose first activity is `outside` (agents living outside study area)
3. Remove agents with activities starting with `freight`

## Output

Same as existing Kelheim runner:

- `{runId}.drt_requests.csv` — DRT requests with budget, times, coordinates
- `{runId}.exmas_rides.csv` — All feasible ride combinations
- `{runId}.person_attributes.csv` — Person attributes for analysis
- `{runId}.mode_cache.csv` — Mode scores per trip
- `{runId}.connection_cache.csv` — Network connections for optimization

Output directory default: `{scenario-path}/../demand-extraction-{sample}pct/`

## What Reuses from `RunKelheimDemandExtraction`

- ExMAS configuration method (all algorithm params)
- Vehicle type network mode fix (`ensureVehicleTypeNetworkModes`)
- Output cleanup logic
- Archive logic
- Freight agent filtering

## What Differs from `RunKelheimDemandExtraction`

| Aspect | Kelheim Runner | Bavaria Runner |
|--------|---------------|----------------|
| Config source | XML from matsim-kelheim repo | Programmatic (hardcoded) |
| Network | Kelheim SVN (9.4k nodes) | Bavaria 30km local (126k nodes) |
| Transit | Kelheim SVN | Bavaria GTFS-derived |
| Population | From config XML | `--population` argument |
| SSL trust store | Yes (SVN downloads) | No (all local files) |
| DRT stops | Kelheim DRT stops from SVN | None (no service area) |
| AV mode | Removed from config | Not present |
| Activity types | Snz-suffixed (`home_600`, etc.) | Simple eqasim names (`home`, etc.) |
| Iteration support | Always 0 | Configurable (0 or N with DMC) |
| Income-dependent scoring | Not wired | Yes (Guice binding) |

## Usage Examples

```bash
# 1% sample, free-flow travel times (quick test)
mvn exec:java -Dexec.mainClass="...RunBavaria30kmDemandExtraction" \
  -Dexec.args="--scenario-path ../matsim_scenarios/bavaria/output/kelheim_30km_100pct \
               --population ../matsim_scenarios/bavaria/output/kelheim_30km_100pct/kelheim_30km_100pct_population.xml.gz \
               --sample 1"

# 1% sample with 50 warm-up iterations
mvn exec:java -Dexec.mainClass="...RunBavaria30kmDemandExtraction" \
  -Dexec.args="--scenario-path ../matsim_scenarios/bavaria/output/kelheim_30km_100pct \
               --population path/to/upsampled_25pct.xml.gz \
               --sample 1 --iterations 50 --dmc-start-rate 0.2 --dmc-end-rate 0.05"

# Restart with converged plans (0 iterations, use baked-in travel times)
mvn exec:java -Dexec.mainClass="...RunBavaria30kmDemandExtraction" \
  -Dexec.args="--scenario-path ../matsim_scenarios/bavaria/output/kelheim_30km_100pct \
               --population path/to/previous-run/output_plans.xml.gz \
               --sample 25 --iterations 0"
```
