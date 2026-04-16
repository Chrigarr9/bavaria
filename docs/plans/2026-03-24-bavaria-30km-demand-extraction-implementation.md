# Bavaria 30km DRT Demand Extraction — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create `RunBavaria30kmDemandExtraction.java` that runs DRT demand extraction on the Bavaria 30km eqasim scenario with Kelheim calibrated scoring and income-dependent marginal utility of money.

**Architecture:** Single Java run class building a MATSim Config programmatically. Reuses ExMAS config and utility methods from `RunKelheimDemandExtraction`. Adds income-dependent scoring via Guice module override, configurable iteration support with DMC annealing, and eqasim-style activity registration.

**Tech Stack:** Java 17, MATSim 2026.0-SNAPSHOT, drt-demand-extraction contrib, vsp contrib (IncomeDependentUtilityOfMoneyPersonScoringParameters)

**Design doc:** `docs/plans/2026-03-24-bavaria-30km-demand-extraction-design.md`

**Reference class:** `src/main/java/org/matsim/contrib/demand_extraction/run/RunKelheimDemandExtraction.java`

---

## File Paths

All paths relative to: `matsim-libs/contribs/drt-demand-extraction/`

- **Create:** `src/main/java/org/matsim/contrib/demand_extraction/run/RunBavaria30kmDemandExtraction.java`
- **Reference (read-only):** `src/main/java/org/matsim/contrib/demand_extraction/run/RunKelheimDemandExtraction.java`
- **Reference (read-only):** `src/main/java/org/matsim/contrib/demand_extraction/demand/DemandExtractionConfigValidator.java`
- **Reference (read-only):** `src/main/java/org/matsim/contrib/demand_extraction/config/ExMasConfigGroup.java`

Build command: `cd matsim-libs/contribs/drt-demand-extraction && mvn compile -DskipTests`

---

### Task 1: Scaffold the class with CLI argument parsing

**Files:**
- Create: `src/main/java/org/matsim/contrib/demand_extraction/run/RunBavaria30kmDemandExtraction.java`

**Step 1: Create the class with main method and CLI parsing**

```java
package org.matsim.contrib.demand_extraction.run;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashSet;
import java.util.Set;

import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;
import org.matsim.api.core.v01.Scenario;
import org.matsim.api.core.v01.TransportMode;
import org.matsim.api.core.v01.population.Activity;
import org.matsim.contrib.demand_extraction.config.ExMasConfigGroup;
import org.matsim.contrib.demand_extraction.config.ExMasConfigGroup.CommuteFilter;
import org.matsim.contrib.demand_extraction.demand.DemandExtractionConfigValidator;
import org.matsim.contrib.demand_extraction.demand.DemandExtractionModule;
import org.matsim.contrib.drt.run.DrtControlerCreator;
import org.matsim.contrib.drt.run.MultiModeDrtConfigGroup;
import org.matsim.contrib.dvrp.run.DvrpConfigGroup;
import org.matsim.core.config.Config;
import org.matsim.core.config.ConfigUtils;
import org.matsim.core.config.groups.ReplanningConfigGroup.StrategySettings;
import org.matsim.core.config.groups.ScoringConfigGroup;
import org.matsim.core.config.groups.ScoringConfigGroup.ActivityParams;
import org.matsim.core.config.groups.ScoringConfigGroup.ModeParams;
import org.matsim.core.controler.Controler;
import org.matsim.core.controler.OutputDirectoryHierarchy;
import org.matsim.core.scenario.ScenarioUtils;
import org.matsim.core.scoring.functions.ScoringParametersForPerson;
import org.matsim.vehicles.VehicleType;

import com.google.inject.Singleton;

import playground.vsp.scoring.IncomeDependentUtilityOfMoneyPersonScoringParameters;

/**
 * Run class for extracting DRT demand from the Bavaria 30km eqasim scenario.
 *
 * <p>Uses Bavaria 30km infrastructure (network, transit, facilities) with Kelheim v3.0
 * calibrated scoring parameters and income-dependent marginal utility of money.
 *
 * <p><b>Prerequisites:</b>
 * <ul>
 *   <li>Bavaria 30km scenario output in {@code --scenario-path}</li>
 *   <li>Population XML (raw eqasim or pre-upsampled) via {@code --population}</li>
 * </ul>
 *
 * <p><b>Usage:</b>
 * <pre>
 * # 1% sample, free-flow travel times
 * mvn exec:java -Dexec.mainClass="org.matsim.contrib.demand_extraction.run.RunBavaria30kmDemandExtraction" \
 *   -Dexec.args="--scenario-path ../matsim_scenarios/bavaria/output/kelheim_30km_100pct \
 *                --population path/to/population.xml.gz --sample 1"
 *
 * # With warm-up iterations and DMC annealing
 * mvn exec:java -Dexec.mainClass="org.matsim.contrib.demand_extraction.run.RunBavaria30kmDemandExtraction" \
 *   -Dexec.args="--scenario-path ../matsim_scenarios/bavaria/output/kelheim_30km_100pct \
 *                --population path/to/upsampled.xml.gz --sample 1 --iterations 50"
 * </pre>
 */
public class RunBavaria30kmDemandExtraction {

    private static final Logger log = LogManager.getLogger(RunBavaria30kmDemandExtraction.class);
    private static final String FILE_PREFIX = "kelheim_30km_100pct_";

    public static void main(String[] args) throws IOException {
        // Parse CLI arguments
        String scenarioPath = null;
        String populationPath = null;
        int sampleSize = 1;
        int iterations = 0;
        double dmcStartRate = 0.2;
        double dmcEndRate = 0.05;
        String outputDir = null;
        boolean deterministic = false;
        Integer algorithmProcessCountArg = null;
        Integer heuristicsProcessCountArg = null;
        boolean cleanup = true;

        for (int i = 0; i < args.length; i++) {
            switch (args[i]) {
                case "--scenario-path" -> scenarioPath = args[++i];
                case "--population" -> populationPath = args[++i];
                case "--sample" -> sampleSize = Integer.parseInt(args[++i]);
                case "--iterations" -> iterations = Integer.parseInt(args[++i]);
                case "--dmc-start-rate" -> dmcStartRate = Double.parseDouble(args[++i]);
                case "--dmc-end-rate" -> dmcEndRate = Double.parseDouble(args[++i]);
                case "--output-dir" -> outputDir = args[++i];
                case "--deterministic" -> deterministic = true;
                case "--algorithm-process-count" -> algorithmProcessCountArg = Integer.parseInt(args[++i]);
                case "--heuristics-process-count" -> heuristicsProcessCountArg = Integer.parseInt(args[++i]);
                case "--no-cleanup" -> cleanup = false;
                default -> log.warn("Unknown argument: {}", args[i]);
            }
        }

        if (scenarioPath == null || populationPath == null) {
            System.err.println("Usage: RunBavaria30kmDemandExtraction "
                    + "--scenario-path <path> --population <path> "
                    + "[--sample <1|10|25|100>] [--iterations <N>] "
                    + "[--dmc-start-rate <0.0-1.0>] [--dmc-end-rate <0.0-1.0>] "
                    + "[--output-dir <path>] [--deterministic]");
            System.exit(1);
        }

        log.info("=== Bavaria 30km DRT Demand Extraction ===");
        log.info("Scenario path: {}", scenarioPath);
        log.info("Population: {}", populationPath);
        log.info("Sample size: {}%", sampleSize);
        log.info("Iterations: {}", iterations);
        if (iterations > 0) {
            log.info("DMC annealing: {}% -> {}%", dmcStartRate * 100, dmcEndRate * 100);
        }

        // Resolve output directory
        Path outDir;
        if (outputDir != null) {
            outDir = Path.of(outputDir);
        } else {
            outDir = Path.of(scenarioPath).getParent()
                    .resolve("demand-extraction-" + sampleSize + "pct");
        }
        Files.createDirectories(outDir);

        // Build config, create scenario, run
        Config config = buildConfig(scenarioPath, populationPath, sampleSize, iterations,
                dmcStartRate, dmcEndRate, deterministic);

        int algorithmProcessCount = algorithmProcessCountArg != null
                ? algorithmProcessCountArg : (deterministic ? 1 : -1);
        int heuristicsProcessCount = heuristicsProcessCountArg != null
                ? heuristicsProcessCountArg : (deterministic ? 1 : -1);
        if (deterministic) {
            config.global().setNumberOfThreads(1);
            config.qsim().setNumberOfThreads(1);
        }

        configureForDemandExtraction(config, outDir, sampleSize, iterations,
                algorithmProcessCount, heuristicsProcessCount, deterministic);
        String runId = config.controller().getRunId();

        DemandExtractionConfigValidator.prepareConfigForDemandExtraction(config);

        Scenario scenario = DrtControlerCreator.createScenarioWithDrtRouteFactory(config);
        ScenarioUtils.loadScenario(scenario);
        ensureVehicleTypeNetworkModes(scenario);

        // Filter unwanted agents
        int originalSize = scenario.getPopulation().getPersons().size();
        filterUnwantedAgents(scenario);
        int filteredSize = scenario.getPopulation().getPersons().size();
        log.info("Filtered population: {} -> {} agents ({} removed)",
                originalSize, filteredSize, originalSize - filteredSize);

        // Create controller with income-dependent scoring
        Controler controler = DrtControlerCreator.createControler(config, scenario, false);
        controler.addOverridingModule(new DemandExtractionModule());
        controler.addOverridingModule(new com.google.inject.AbstractModule() {
            @Override
            protected void configure() {
                bind(ScoringParametersForPerson.class)
                        .to(IncomeDependentUtilityOfMoneyPersonScoringParameters.class)
                        .in(Singleton.class);
            }
        });

        controler.run();

        if (cleanup) {
            RunKelheimDemandExtraction.cleanupOutputDirectory(outDir, runId);
        }

        log.info("\n=== Demand Extraction Complete ===");
        log.info("Output directory: {}", outDir.toAbsolutePath());
        log.info("Demand extraction files in: {}/drt_demand/", outDir.toAbsolutePath());
        log.info("===================================\n");
    }

    // ... (methods added in subsequent tasks)
}
```

**Step 2: Verify it compiles (stub methods needed)**

Add empty stubs for the methods referenced in `main`:

```java
    private static Config buildConfig(String scenarioPath, String populationPath,
            int sampleSize, int iterations, double dmcStartRate, double dmcEndRate,
            boolean deterministic) {
        throw new UnsupportedOperationException("TODO");
    }

    private static void configureForDemandExtraction(Config config, Path outputDir,
            int sampleSize, int iterations, int algorithmProcessCount,
            int heuristicsProcessCount, boolean deterministic) {
        throw new UnsupportedOperationException("TODO");
    }

    private static void filterUnwantedAgents(Scenario scenario) {
        throw new UnsupportedOperationException("TODO");
    }

    private static void ensureVehicleTypeNetworkModes(Scenario scenario) {
        // Reuse from RunKelheimDemandExtraction
        int fixed = 0;
        fixed += ensureVehicleTypeNetworkModes(scenario.getVehicles().getVehicleTypes().values());
        fixed += ensureVehicleTypeNetworkModes(scenario.getTransitVehicles().getVehicleTypes().values());
        if (fixed > 0) {
            log.warn("Set missing vehicleType networkMode for {} type(s) to 'car'", fixed);
        }
    }

    private static int ensureVehicleTypeNetworkModes(java.util.Collection<VehicleType> types) {
        int fixed = 0;
        for (VehicleType type : types) {
            try {
                String nm = type.getNetworkMode();
                if (nm == null || nm.isBlank()) {
                    type.setNetworkMode(TransportMode.car);
                    fixed++;
                }
            } catch (NullPointerException e) {
                type.setNetworkMode(TransportMode.car);
                fixed++;
            }
        }
        return fixed;
    }
```

**Step 3: Compile**

Run: `cd matsim-libs/contribs/drt-demand-extraction && mvn compile -DskipTests`
Expected: BUILD SUCCESS (with stub methods throwing UnsupportedOperationException)

**Step 4: Commit**

```bash
git add src/main/java/org/matsim/contrib/demand_extraction/run/RunBavaria30kmDemandExtraction.java
git commit -m "feat: scaffold RunBavaria30kmDemandExtraction with CLI parsing"
```

---

### Task 2: Implement `buildConfig` — input files and Kelheim scoring

**Files:**
- Modify: `src/main/java/org/matsim/contrib/demand_extraction/run/RunBavaria30kmDemandExtraction.java`

**Step 1: Replace the `buildConfig` stub with the full implementation**

```java
    /**
     * Build a MATSim Config programmatically using Bavaria 30km infrastructure
     * and Kelheim v3.0 calibrated scoring parameters.
     */
    private static Config buildConfig(String scenarioPath, String populationPath,
            int sampleSize, int iterations, double dmcStartRate, double dmcEndRate,
            boolean deterministic) {

        Config config = ConfigUtils.createConfig(
                new ExMasConfigGroup(),
                new MultiModeDrtConfigGroup(),
                new DvrpConfigGroup());

        // --- Input files from Bavaria 30km scenario ---
        Path base = Path.of(scenarioPath);
        config.network().setInputFile(base.resolve(FILE_PREFIX + "network.xml.gz").toString());
        config.transit().setTransitScheduleFile(base.resolve(FILE_PREFIX + "transit_schedule.xml.gz").toString());
        config.transit().setVehiclesFile(base.resolve(FILE_PREFIX + "transit_vehicles.xml.gz").toString());
        config.vehicles().setVehiclesFile(base.resolve(FILE_PREFIX + "vehicles.xml.gz").toString());
        config.facilities().setInputFile(base.resolve(FILE_PREFIX + "facilities.xml.gz").toString());
        config.plans().setInputFile(populationPath);
        config.transit().setUseTransit(true);

        // --- Global settings ---
        config.global().setCoordinateSystem("EPSG:25832");
        config.global().setNumberOfThreads(6);

        // --- QSim ---
        double sampleFactor = sampleSize / 100.0;
        config.qsim().setFlowCapFactor(sampleFactor);
        config.qsim().setStorageCapFactor(sampleFactor);
        config.qsim().setMainModes(java.util.List.of("car"));
        config.qsim().setNumberOfThreads(8);
        config.qsim().setStartTime(0);
        config.qsim().setEndTime(36 * 3600);
        config.qsim().setTrafficDynamics(
                org.matsim.core.config.groups.QSimConfigGroup.TrafficDynamics.kinematicWaves);
        config.qsim().setVehiclesSource(
                org.matsim.core.config.groups.QSimConfigGroup.VehiclesSource.modeVehicleTypesFromVehiclesData);

        // --- Routing ---
        config.routing().setNetworkModes(java.util.List.of("car"));

        // --- Kelheim v3.0 calibrated scoring ---
        applyKelheimScoring(config);

        // --- Activity params for eqasim activity types ---
        registerEqasimActivities(config);

        // --- Replanning (only if iterations > 0) ---
        if (iterations > 0) {
            configureReplanning(config, iterations, dmcStartRate, dmcEndRate);
        }

        log.info("Config built: {} iterations, {}% sample, Kelheim v3.0 scoring",
                iterations, sampleSize);
        return config;
    }
```

**Step 2: Implement `applyKelheimScoring`**

```java
    /**
     * Apply Kelheim v3.0 calibrated scoring parameters.
     * Values from kelheim-v3.0-25pct.kexi.config.xml.
     * These were calibrated WITH income-dependent marginalUtilityOfMoney active.
     */
    private static void applyKelheimScoring(Config config) {
        ScoringConfigGroup scoring = config.scoring();

        scoring.setPerforming_utils_hr(6.0);
        scoring.setMarginalUtilityOfMoney(1.0);
        scoring.setLateArrival_utils_hr(-18.0);
        scoring.setUtilityOfLineSwitch(-1.0);

        // PT waiting disutility
        ScoringConfigGroup.ScoringParameterSet params = scoring.getOrCreateScoringParameters(null);
        params.setMarginalUtlOfWaitingPt_utils_hr(-1.6);

        // --- Mode params (from kelheim-v3.0-25pct.kexi.config.xml) ---

        // car: ASC=0.109, dailyMonetary=-5.3, monetaryDistRate=-2.0E-4
        ModeParams car = new ModeParams(TransportMode.car);
        car.setConstant(0.10908902922956654);
        car.setMarginalUtilityOfTraveling(0.0);
        car.setMarginalUtilityOfDistance(0.0);
        car.setMonetaryDistanceRate(-2.0E-4);
        car.setDailyMonetaryConstant(-5.3);
        scoring.addModeParams(car);

        // ride: ASC=-0.449, margUtilTravel=-12.0, monetaryDistRate=-2.0E-4
        ModeParams ride = new ModeParams(TransportMode.ride);
        ride.setConstant(-0.44874536876610344);
        ride.setMarginalUtilityOfTraveling(-12.0);
        ride.setMarginalUtilityOfDistance(0.0);
        ride.setMonetaryDistanceRate(-2.0E-4);
        scoring.addModeParams(ride);

        // pt: ASC=0.045
        ModeParams pt = new ModeParams(TransportMode.pt);
        pt.setConstant(0.0449751479497542);
        pt.setMarginalUtilityOfTraveling(0.0);
        pt.setMarginalUtilityOfDistance(0.0);
        pt.setMonetaryDistanceRate(0.0);
        scoring.addModeParams(pt);

        // bike: ASC=-0.906, margUtilTravel=-3.0
        ModeParams bike = new ModeParams(TransportMode.bike);
        bike.setConstant(-0.9059637590522914);
        bike.setMarginalUtilityOfTraveling(-3.0);
        bike.setMarginalUtilityOfDistance(0.0);
        bike.setMonetaryDistanceRate(0.0);
        scoring.addModeParams(bike);

        // walk: all zero
        ModeParams walk = new ModeParams(TransportMode.walk);
        walk.setConstant(0.0);
        walk.setMarginalUtilityOfTraveling(0.0);
        walk.setMarginalUtilityOfDistance(0.0);
        walk.setMonetaryDistanceRate(0.0);
        scoring.addModeParams(walk);

        // drt: ASC=2.45, margUtilDist=-2.5E-4 (non-monetary distance disutility)
        ModeParams drt = new ModeParams("drt");
        drt.setConstant(2.45);
        drt.setMarginalUtilityOfTraveling(0.0);
        drt.setMarginalUtilityOfDistance(-2.5E-4);
        drt.setMonetaryDistanceRate(0.0);
        scoring.addModeParams(drt);

        // freight: for any remaining freight agents
        ModeParams freight = new ModeParams("freight");
        freight.setConstant(0.0);
        freight.setMarginalUtilityOfTraveling(0.0);
        freight.setMonetaryDistanceRate(-0.002);
        scoring.addModeParams(freight);

        log.info("Applied Kelheim v3.0 calibrated scoring parameters");
        log.info("  marginalUtilityOfMoney: {} (config-level, person-specific via income scaling)",
                scoring.getMarginalUtilityOfMoney());
    }
```

**Step 3: Implement `registerEqasimActivities`**

```java
    /**
     * Register eqasim activity types with sensible typical durations.
     * Bavaria eqasim uses simple names (home, work, etc.), not Snz-suffixed.
     */
    private static void registerEqasimActivities(Config config) {
        ScoringConfigGroup scoring = config.scoring();

        // Scored activities with typical durations
        addActivityParams(scoring, "home", 12 * 3600, true);
        addActivityParams(scoring, "work", 8 * 3600, true);
        addActivityParams(scoring, "education", 6 * 3600, true);
        addActivityParams(scoring, "shop", 1 * 3600, true);
        addActivityParams(scoring, "leisure", 2 * 3600, true);
        addActivityParams(scoring, "other", 2 * 3600, true);

        // Non-scored activities
        addActivityParams(scoring, "outside", -1, false);
        addActivityParams(scoring, "freight_loading", -1, false);
        addActivityParams(scoring, "freight_unloading", -1, false);

        // Interaction activities (never scored)
        for (String mode : new String[]{"car", "pt", "bike", "walk", "drt", "ride",
                "taxi", "other", "car_passenger"}) {
            addActivityParams(scoring, mode + " interaction", -1, false);
        }

        log.info("Registered {} eqasim activity types", scoring.getActivityParams().size());
    }

    private static void addActivityParams(ScoringConfigGroup scoring, String type,
            double typicalDuration, boolean scored) {
        ActivityParams params = new ActivityParams(type);
        if (typicalDuration > 0) {
            params.setTypicalDuration(typicalDuration);
        }
        params.setScoringThisActivityAtAll(scored);
        scoring.addActivityParams(params);
    }
```

**Step 4: Compile**

Run: `cd matsim-libs/contribs/drt-demand-extraction && mvn compile -DskipTests`
Expected: BUILD SUCCESS

**Step 5: Commit**

```bash
git add -u
git commit -m "feat: implement buildConfig with Kelheim scoring and eqasim activities"
```

---

### Task 3: Implement `configureReplanning` — iteration strategy with DMC annealing

**Files:**
- Modify: `src/main/java/org/matsim/contrib/demand_extraction/run/RunBavaria30kmDemandExtraction.java`

**Step 1: Implement `configureReplanning`**

```java
    /**
     * Configure replanning strategies with SubtourModeChoice annealing.
     * Only called when iterations > 0.
     */
    private static void configureReplanning(Config config, int iterations,
            double dmcStartRate, double dmcEndRate) {
        config.controller().setLastIteration(iterations);

        // Strategy weights
        config.replanning().setFractionOfIterationsToDisableInnovation(0.9);

        StrategySettings changeExpBeta = new StrategySettings();
        changeExpBeta.setStrategyName("ChangeExpBeta");
        changeExpBeta.setSubpopulation("person");
        changeExpBeta.setWeight(0.85);
        config.replanning().addStrategySettings(changeExpBeta);

        StrategySettings reRoute = new StrategySettings();
        reRoute.setStrategyName("ReRoute");
        reRoute.setSubpopulation("person");
        reRoute.setWeight(0.10);
        config.replanning().addStrategySettings(reRoute);

        StrategySettings subtourModeChoice = new StrategySettings();
        subtourModeChoice.setStrategyName("SubtourModeChoice");
        subtourModeChoice.setSubpopulation("person");
        subtourModeChoice.setWeight(dmcStartRate);
        config.replanning().addStrategySettings(subtourModeChoice);

        StrategySettings timeMutator = new StrategySettings();
        timeMutator.setStrategyName("TimeAllocationMutator");
        timeMutator.setSubpopulation("person");
        timeMutator.setWeight(0.10);
        config.replanning().addStrategySettings(timeMutator);

        // TimeAllocationMutator range
        config.timeAllocationMutator().setMutationRange(7200.0);

        // SubtourModeChoice config
        config.subtourModeChoice().setModes(new String[]{"car", "pt", "bike", "walk"});
        config.subtourModeChoice().setChainBasedModes(new String[]{"car", "bike"});
        config.subtourModeChoice().setConsiderCarAvailability(true);
        config.subtourModeChoice().setBehavior(
                org.matsim.core.config.groups.SubtourModeChoiceConfigGroup.Behavior.betweenAllAndFewerConstraints);
        config.subtourModeChoice().setProbaForRandomSingleTripMode(0.5);

        // ReplanningAnnealer — sigmoid anneal SubtourModeChoice from startRate to endRate
        var annealerModule = config.getModules().get("ReplanningAnnealer");
        if (annealerModule == null) {
            var annealerConfig = new org.matsim.core.config.groups.ReplanningAnnealerConfigGroup();
            annealerConfig.setActivateAnnealingModule(true);

            var annealVar = new org.matsim.core.config.groups.ReplanningAnnealerConfigGroup.AnnealingVariable();
            annealVar.setAnnealParameter("globalInnovationRate");
            annealVar.setAnnealType("sigmoid");
            annealVar.setDefaultSubpopulation("person");
            annealVar.setHalfLife(0.5);
            annealVar.setShapeFactor(0.01);
            annealVar.setStartValue(dmcStartRate / (dmcStartRate + 0.10 + 0.10)); // normalize
            annealVar.setEndValue(dmcEndRate / (dmcEndRate + 0.10 + 0.10));
            annealerConfig.addParameterSet(annealVar);

            config.addModule(annealerConfig);
        }

        // Score averaging
        config.scoring().setFractionOfIterationsToStartScoreMSA(0.9);

        log.info("Replanning configured: {} iterations, DMC {}% -> {}%",
                iterations, dmcStartRate * 100, dmcEndRate * 100);
    }
```

**Step 2: Compile**

Run: `cd matsim-libs/contribs/drt-demand-extraction && mvn compile -DskipTests`
Expected: BUILD SUCCESS

**Step 3: Commit**

```bash
git add -u
git commit -m "feat: add configureReplanning with DMC annealing"
```

---

### Task 4: Implement `configureForDemandExtraction` and `filterUnwantedAgents`

**Files:**
- Modify: `src/main/java/org/matsim/contrib/demand_extraction/run/RunBavaria30kmDemandExtraction.java`

**Step 1: Replace the `configureForDemandExtraction` stub**

```java
    /**
     * Configure MATSim for demand extraction: output settings, ExMAS config,
     * VSP defaults, iteration count.
     */
    private static void configureForDemandExtraction(Config config, Path outputDir,
            int sampleSize, int iterations, int algorithmProcessCount,
            int heuristicsProcessCount, boolean deterministic) {

        // VSP defaults
        config.vspExperimental().setVspDefaultsCheckingLevel(
                org.matsim.core.config.groups.VspExperimentalConfigGroup.VspDefaultsCheckingLevel.info);

        // Output settings
        config.controller().setOutputDirectory(outputDir.toString());
        config.controller().setOverwriteFileSetting(
                OutputDirectoryHierarchy.OverwriteFileSetting.deleteDirectoryIfExists);
        config.controller().setRunId("bavaria-30km-" + sampleSize + "pct-exmas");

        if (iterations == 0) {
            config.controller().setLastIteration(0);
        }
        // else: already set by configureReplanning

        config.controller().setWriteEventsInterval(iterations > 0 ? 50 : 0);
        config.controller().setWritePlansInterval(iterations > 0 ? 50 : 0);
        config.controller().setRoutingAlgorithmType(
                org.matsim.core.config.groups.ControllerConfigGroup.RoutingAlgorithmType.SpeedyALT);

        // DVRP network modes
        DvrpConfigGroup dvrp = ConfigUtils.addOrGetModule(config, DvrpConfigGroup.class);
        dvrp.setNetworkModes(java.util.Collections.singleton("drt"));

        // Configure ExMAS (same as RunKelheimDemandExtraction)
        configureExMas(config, algorithmProcessCount, heuristicsProcessCount, deterministic);

        logScoringParameters(config);
    }
```

**Step 2: Copy `configureExMas` from `RunKelheimDemandExtraction`**

Copy the method verbatim — it's identical. The ExMAS algorithm configuration does not change between scenarios. See `RunKelheimDemandExtraction.java:579-678`.

**Step 3: Copy `logScoringParameters` from `RunKelheimDemandExtraction`**

Copy the method verbatim. See `RunKelheimDemandExtraction.java:721-749`. Update the mode list to include "drt" (it already does).

**Step 4: Replace the `filterUnwantedAgents` stub**

```java
    /**
     * Filter out freight, truck, and outside agents from the population.
     * These agents don't have normal commute patterns and can cause issues.
     */
    private static void filterUnwantedAgents(Scenario scenario) {
        log.info("Filtering unwanted agents...");

        scenario.getPopulation().getPersons().values().removeIf(person -> {
            // Check subpopulation
            Object subpop = person.getAttributes().getAttribute("subpopulation");
            if ("freight".equals(subpop) || "truck".equals(subpop)) {
                return true;
            }

            // Check for freight or outside activities
            if (person.getSelectedPlan() != null) {
                return person.getSelectedPlan().getPlanElements().stream()
                        .filter(Activity.class::isInstance)
                        .map(Activity.class::cast)
                        .anyMatch(act -> act.getType() != null
                                && (act.getType().startsWith("freight")
                                    || "outside".equals(act.getType())));
            }

            return false;
        });
    }
```

**Step 5: Also make `cleanupOutputDirectory` accessible**

The `main` method calls `RunKelheimDemandExtraction.cleanupOutputDirectory`. Check if that method is package-private or private. If private, either:
- Change it to `static` package-private in `RunKelheimDemandExtraction`, OR
- Copy the method into the new class

Looking at the existing code (line 336), it's `private static`. Change it to package-private by removing the `private` modifier in `RunKelheimDemandExtraction.java:336`:

```java
// Change from:
private static void cleanupOutputDirectory(Path outputDir, String runId) {
// To:
static void cleanupOutputDirectory(Path outputDir, String runId) {
```

Also make `deleteRecursively` package-private (line 372):

```java
// Change from:
private static void deleteRecursively(Path path) throws IOException {
// To:
static void deleteRecursively(Path path) throws IOException {
```

**Step 6: Compile**

Run: `cd matsim-libs/contribs/drt-demand-extraction && mvn compile -DskipTests`
Expected: BUILD SUCCESS

**Step 7: Commit**

```bash
git add -u
git commit -m "feat: implement configureForDemandExtraction, filterUnwantedAgents, ExMAS config"
```

---

### Task 5: Smoke test with 1% sample

**Step 1: Build the project**

Run: `cd matsim-libs/contribs/drt-demand-extraction && mvn clean install -DskipTests`
Expected: BUILD SUCCESS

**Step 2: Run with 1% sample, 0 iterations (quick smoke test)**

```bash
cd matsim-libs/contribs/drt-demand-extraction
mvn exec:java \
  -Dexec.mainClass="org.matsim.contrib.demand_extraction.run.RunBavaria30kmDemandExtraction" \
  -Dexec.args="--scenario-path ../../../matsim_scenarios/bavaria/output/kelheim_30km_100pct --population ../../../matsim_scenarios/bavaria/output/kelheim_30km_100pct/kelheim_30km_100pct_population.xml.gz --sample 1 --deterministic"
```

Expected:
- Logs show "Bavaria 30km DRT Demand Extraction"
- Kelheim v3.0 scoring parameters logged
- Income-dependent marginalUtilityOfMoney computed (log line "global average income is ...")
- Population loaded and filtered
- Iteration 0 runs
- DemandExtractionModule fires
- Output files created in `demand-extraction-1pct/drt_demand/`

**Step 3: Verify output files exist**

```bash
ls ../../../matsim_scenarios/bavaria/output/demand-extraction-1pct/drt_demand/
```

Expected: `bavaria-30km-1pct-exmas.drt_requests.csv`, `bavaria-30km-1pct-exmas.exmas_rides.csv`, etc.

**Step 4: Spot-check the requests CSV**

```bash
head -5 ../../../matsim_scenarios/bavaria/output/demand-extraction-1pct/drt_demand/bavaria-30km-1pct-exmas.drt_requests.csv
```

Expected: CSV with columns including personId, budget, departureTime, originX, originY, etc. Budget values should be plausible (positive and negative, range roughly -10 to +20).

**Step 5: Commit any fixes**

```bash
git add -u
git commit -m "fix: adjustments from smoke test"
```

---

### Task 6: Test with warm-up iterations (optional, long-running)

**Step 1: Run with 5 iterations on 1% sample**

```bash
cd matsim-libs/contribs/drt-demand-extraction
mvn exec:java \
  -Dexec.mainClass="org.matsim.contrib.demand_extraction.run.RunBavaria30kmDemandExtraction" \
  -Dexec.args="--scenario-path ../../../matsim_scenarios/bavaria/output/kelheim_30km_100pct --population ../../../matsim_scenarios/bavaria/output/kelheim_30km_100pct/kelheim_30km_100pct_population.xml.gz --sample 1 --iterations 5 --dmc-start-rate 0.2 --dmc-end-rate 0.05 --deterministic"
```

Expected:
- 5 iterations run with replanning
- Mode shares logged per iteration
- Score convergence visible
- Demand extraction fires after iteration 5

**Step 2: Verify output differs from 0-iteration run**

The travel times should differ (congested vs free-flow), potentially affecting budgets.

**Step 3: Commit any fixes**

```bash
git add -u
git commit -m "fix: iteration warm-up adjustments"
```

---

## Summary

| Task | Description | Estimated Effort |
|------|-------------|-----------------|
| 1 | Scaffold class + CLI parsing + stubs | ~15 min |
| 2 | `buildConfig` + Kelheim scoring + eqasim activities | ~20 min |
| 3 | `configureReplanning` with DMC annealing | ~10 min |
| 4 | `configureForDemandExtraction` + ExMAS + filtering | ~15 min |
| 5 | Smoke test with 1% sample | ~15 min (+ run time) |
| 6 | Warm-up iteration test (optional) | ~10 min (+ run time) |

Total implementation: ~1.5 hours + MATSim run time.
