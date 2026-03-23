
import pandas as pd
import os
import numpy as np

"""
Apply gravity model to generate a distance matrix for Oberbayern.
Optionally constrained by official BA Pendlerverflechtungen at Kreis level.
"""

DEFAULT_SLOPE = -0.2 # -0.09 came from IDF, value -2.0 has been calibrated
DEFAULT_CONSTANT = -2.4
DEFAULT_DIAGONAL = 1.0

def configure(context):
    context.stage("bavaria.gravity.distance_matrix")
    context.stage("bavaria.ipf.attributed")
    context.stage("bavaria.data.census.employees")
    context.config("gravity_slope", DEFAULT_SLOPE)
    context.config("gravity_constant", DEFAULT_CONSTANT)
    context.config("gravity_diagonal", DEFAULT_DIAGONAL)
    context.config("pendler_od_path", None)
    context.config("gemeinde_od_path", None)
    context.config("gemeinde_iop_path", None)
    context.config("gemeinde_eckzahlen_path", None)
    context.config("data_path")
    context.config("bavaria.work_flow_path", "bavaria/a6502c_202200.xlsx")

def evaluate_gravity(population, employees, friction):
    # Initizlize production, attraction, and flow
    production = np.ones((len(population),))
    attraction = np.ones((len(population),))
    flow = np.ones((len(population), len(population)))
    converged = False

    # Perform maximum 100 iterations (but convergence will hopefully happen earlier)
    for iteration in range(int(1e6)):
        # Backup to calculate change
        previous_production = np.copy(production)
        previous_attraction = np.copy(attraction)
        previous_flow = np.copy(flow)

        # Calculate production terms
        for k in range(len(population)):
            production[k] = population[k] / np.sum(attraction * friction[k,:])

        # Calculate attraction terms
        for k in range(len(population)):
            attraction[k] = employees[k] / np.sum(production * friction[:,k])

        # Initialize new flow matrix
        flow = np.copy(friction)

        # Apply production terms
        for i in range(len(population)):
            flow[i,:] *= production[i]

        # Apply attraction terms
        for j in range(len(population)):
            flow[:,j] *= attraction[j]

        # Calculate change to previous iteration
        production_delta = np.abs(production - previous_production)
        attraction_delta = np.abs(attraction - previous_attraction)
        flow_delta = np.abs(flow - previous_flow)

        print("Gravity iteration", iteration,
            "prod. max. Δ:", np.max(production_delta),
            "attr. max. Δ:", np.max(attraction_delta),
            "flow max. Δ:", np.max(flow_delta),
        )

        # Stop if change is sufficiently small
        if np.max(production_delta) < 1e-3 and np.max(attraction_delta) < 1e-3 and np.max(flow_delta) < 1e-3:
            converged = True
            break

    assert converged
    return flow


def build_pendler_constrained_matrix(municipalities, df_employees, df_distances,
                                      pendler_shares, study_kreise, slope):
    """
    Build Gemeinde x Gemeinde OD probability matrix constrained by Kreis-level Pendler shares.

    P(g_j | g_i) = P_pendler(K_d | K_o) * P_gravity(g_j | K_d, g_i)

    Where P_gravity uses employee count * distance decay within each destination Kreis.
    """
    # Build Gemeinde -> Kreis lookup (first 5 digits of commune_id)
    gem_to_kreis = {m: m[:5] for m in municipalities}

    # Group Gemeinden by Kreis
    kreis_to_gems = {}
    for m, k in gem_to_kreis.items():
        kreis_to_gems.setdefault(k, []).append(m)

    # Index employees
    emp_lookup = dict(zip(df_employees["destination_id"], df_employees["employees"]))

    # Index distances into a fast lookup
    dist_lookup = {}
    for _, row in df_distances.iterrows():
        dist_lookup[(row["origin_id"], row["destination_id"])] = row["distance_km"]

    # Build Pendler share lookup: {(origin_kreis, dest_kreis): share}
    pendler_lookup = {}
    for _, row in pendler_shares.iterrows():
        pendler_lookup[(row["origin_kreis"], row["destination_kreis"])] = row["share"]

    rows = []
    for origin in municipalities:
        origin_kreis = gem_to_kreis[origin]
        origin_total_weight = 0.0
        origin_rows = []

        for dest_kreis in sorted(set(gem_to_kreis.values())):
            pendler_share = pendler_lookup.get((origin_kreis, dest_kreis), 0.0)
            if pendler_share <= 0:
                continue

            # Gravity weights within destination Kreis
            dest_gems = kreis_to_gems.get(dest_kreis, [])
            gravity_weights = []
            for dest in dest_gems:
                emp = emp_lookup.get(dest, 0)
                dist = dist_lookup.get((origin, dest), 50.0)  # default 50km if missing
                w = emp * np.exp(slope * dist)
                gravity_weights.append((dest, w))

            grav_total = sum(w for _, w in gravity_weights)
            if grav_total <= 0:
                # No employees in this Kreis — distribute evenly
                grav_total = len(gravity_weights) if gravity_weights else 1
                gravity_weights = [(d, 1.0) for d, _ in gravity_weights]

            for dest, gw in gravity_weights:
                weight = pendler_share * (gw / grav_total)
                origin_rows.append({"origin_id": origin, "destination_id": dest, "weight": weight})
                origin_total_weight += weight

        # Renormalize (drop _outside share, renormalize remaining to 1.0)
        if origin_total_weight > 0:
            for row in origin_rows:
                row["weight"] /= origin_total_weight
        elif origin_rows:
            # Fallback: equal distribution
            for row in origin_rows:
                row["weight"] = 1.0 / len(origin_rows)

        rows.extend(origin_rows)

    return pd.DataFrame(rows)


def build_gemeinde_constrained_matrix(municipalities, df_employees, df_distances,
                                       gemeinde_od, study_kreise, slope,
                                       total_auspendler=None,
                                       pendler_outside_fractions=None):
    """
    Build Gemeinde x Gemeinde OD matrix using three data layers:

    1. IOP (exact internal commuter counts per Gemeinde)
    2. Verfl top-10 (exact cross-Gemeinde flows, within + outside study area)
    3. Kreis-level Pendler outside fraction (for the remaining tail)
    4. Gravity fill only for the small within-study remainder not in top-10

    The outside fraction for the remaining tail (not in top-10) comes from
    the Kreis-level Pendler data, not from a proportional assumption.

    Args:
        pendler_outside_fractions: Dict {kreis_5digit: outside_fraction} from
            Kreis-level Pendler shares. Used for the remaining tail's outside split.
        total_auspendler: Dict {commune_id: total_auspendler_count} from Eckzahlen.

    Returns:
        (df_weights, df_outside)
    """
    mun_set = set(municipalities)

    emp_lookup = dict(zip(df_employees["destination_id"], df_employees["employees"]))
    dist_lookup = {}
    for _, row in df_distances.iterrows():
        dist_lookup[(row["origin_id"], row["destination_id"])] = row["distance_km"]

    od_by_origin = {}
    for _, row in gemeinde_od.iterrows():
        od_by_origin.setdefault(row["origin_id"], []).append(row)

    weight_rows = []
    outside_rows = []

    for origin in municipalities:
        flows = od_by_origin.get(origin, [])
        origin_kreis = origin[:5]

        # Layer 1+2: Separate IOP (internal) and Verfl (cross-Gemeinde)
        internal_count = 0
        known_within = {}
        outside_count = 0

        for f in flows:
            dest = f["destination_id"]
            count = f["count"]
            if dest == origin:
                internal_count += count
            elif dest in mun_set:
                known_within[dest] = known_within.get(dest, 0) + count
            else:
                outside_count += count

        # Compute remaining tail (not in IOP + top-10)
        known_cross_total = sum(known_within.values())
        top10_total = known_cross_total + outside_count

        if total_auspendler is not None and origin in total_auspendler:
            remaining = max(0, total_auspendler[origin] - top10_total)
        else:
            remaining = known_cross_total * 0.37

        # Layer 3: Split remaining using Kreis-level outside fraction
        if pendler_outside_fractions is not None and origin_kreis in pendler_outside_fractions:
            kreis_outside_frac = pendler_outside_fractions[origin_kreis]
        else:
            # Fallback: use the top-10 within/outside ratio
            kreis_outside_frac = outside_count / top10_total if top10_total > 0 else 0.5

        remaining_outside = remaining * kreis_outside_frac
        remaining_within = remaining * (1 - kreis_outside_frac)
        outside_count += remaining_outside

        # Layer 4: Gravity fill for the within-study remainder
        known_dests = set(known_within.keys()) | {origin}
        fill_dests = [m for m in municipalities if m != origin and m not in known_dests]

        gravity_weights = {}
        for dest in fill_dests:
            emp = emp_lookup.get(dest, 0)
            dist = dist_lookup.get((origin, dest), 50.0)
            w = emp * np.exp(slope * dist)
            if w > 0:
                gravity_weights[dest] = w

        # Build raw weights (counts)
        raw = {}
        raw[origin] = internal_count

        for dest, count in known_within.items():
            raw[dest] = count

        grav_total = sum(gravity_weights.values())
        if grav_total > 0 and remaining_within > 0:
            for dest, gw in gravity_weights.items():
                raw[dest] = raw.get(dest, 0) + remaining_within * (gw / grav_total)

        # Outside fraction
        total_within = sum(raw.values())
        total_all = total_within + outside_count
        outside_frac = outside_count / total_all if total_all > 0 else 0.0

        # Normalize within-study weights to 1.0
        if total_within > 0:
            for dest, w in raw.items():
                weight_rows.append({
                    "origin_id": origin,
                    "destination_id": dest,
                    "weight": w / total_within,
                })
        else:
            weight_rows.append({
                "origin_id": origin,
                "destination_id": origin,
                "weight": 1.0,
            })

        outside_rows.append({
            "commune_id": origin,
            "outside_fraction": outside_frac,
        })

    df_weights = pd.DataFrame(weight_rows)
    df_outside = pd.DataFrame(outside_rows)

    return df_weights, df_outside


def execute(context):
    # Load data
    df_distances = context.stage("bavaria.gravity.distance_matrix")
    df_population = context.stage("bavaria.ipf.attributed")
    df_employees = context.stage("bavaria.data.census.employees")

    # Manage identifiers
    df_population = df_population.rename(columns = {
        "commune_id": "origin_id",
        "weight": "population"
    })[["origin_id", "population"]]

    df_employees = df_employees.rename(columns = {
        "commune_id": "destination_id",
        "weight": "employees"
    })[["destination_id", "employees"]]

    # Aggregate population
    df_population = df_population.groupby("origin_id")["population"].sum().reset_index()

    # Find the set of used municipalities (also taking into account zero flows)
    municipalities = set(df_population["origin_id"])
    municipalities |= set(df_employees["destination_id"])
    municipalities |= set(df_distances["origin_id"])
    municipalities |= set(df_distances["destination_id"])
    municipalities = sorted(list(municipalities))

    gemeinde_od_path = context.config("gemeinde_od_path")
    pendler_od_path = context.config("pendler_od_path")

    if gemeinde_od_path is not None:
        # === GEMEINDE-LEVEL OD MODE (most precise) ===
        from bavaria.gravity.pendler_data import (
            parse_gemeinde_od, load_total_auspendler,
            parse_pendler_matrix, load_employed_at_wohnort
        )

        data_path = context.config("data_path")
        full_verfl_path = "{}/{}".format(data_path, gemeinde_od_path)
        full_iop_path = "{}/{}".format(data_path, context.config("gemeinde_iop_path"))

        study_kreise = set(m[:5] for m in municipalities)
        print(f"Gemeinde-level OD mode: {len(study_kreise)} Kreise, {len(municipalities)} Gemeinden")

        gemeinde_od = parse_gemeinde_od(full_verfl_path, full_iop_path, set(municipalities))

        # Load exact Auspendler totals from Eckzahlen
        eckzahlen_path = context.config("gemeinde_eckzahlen_path")
        ausp_totals = None
        if eckzahlen_path is not None:
            full_eck_path = "{}/{}".format(data_path, eckzahlen_path)
            ausp_totals = load_total_auspendler(full_eck_path, set(municipalities))
            print(f"Loaded Auspendler totals for {len(ausp_totals)} Gemeinden from Eckzahlen")

        # Load Kreis-level outside fractions for the remaining tail
        pendler_outside = None
        if context.config("pendler_od_path") is not None:
            full_pendler_path = "{}/{}".format(data_path, context.config("pendler_od_path"))
            a6502c_path = "{}/{}".format(data_path, context.config("bavaria.work_flow_path"))
            wohnort = load_employed_at_wohnort(a6502c_path, study_kreise)
            pendler_shares = parse_pendler_matrix(full_pendler_path, study_kreise, wohnort)
            pendler_outside = {}
            for _, row in pendler_shares.iterrows():
                if row["destination_kreis"] == "_outside":
                    pendler_outside[row["origin_kreis"]] = row["share"]
            print(f"Loaded Kreis-level outside fractions for {len(pendler_outside)} Kreise")

        slope = context.config("gravity_slope")
        df_work_matrix, df_outside = build_gemeinde_constrained_matrix(
            municipalities,
            df_employees.reset_index() if "destination_id" not in df_employees.columns else df_employees,
            df_distances.reset_index() if "origin_id" not in df_distances.columns else df_distances,
            gemeinde_od, study_kreise, slope,
            total_auspendler=ausp_totals,
            pendler_outside_fractions=pendler_outside
        )

        n_affected = (df_outside["outside_fraction"] > 0).sum()
        print(f"Outside commuter fractions: {n_affected} municipalities affected, "
              f"range {df_outside['outside_fraction'].min():.1%}-{df_outside['outside_fraction'].max():.1%}")

        # Education: pure gravity (no Pendler data for education)
        df_education_matrix = _build_pure_gravity(
            context, municipalities, df_population, df_employees, df_distances
        )

        return df_work_matrix, df_education_matrix, df_outside

    elif pendler_od_path is not None:
        # === KREIS-LEVEL PENDLER MODE ===
        from bavaria.gravity.pendler_data import parse_pendler_matrix, load_employed_at_wohnort

        data_path = context.config("data_path")
        full_pendler_path = "{}/{}".format(data_path, pendler_od_path)
        a6502c_path = "{}/{}".format(data_path, context.config("bavaria.work_flow_path"))

        study_kreise = set(m[:5] for m in municipalities)
        print(f"Pendler-constrained mode: {len(study_kreise)} Kreise, {len(municipalities)} Gemeinden")

        wohnort = load_employed_at_wohnort(a6502c_path, study_kreise)
        pendler_shares = parse_pendler_matrix(full_pendler_path, study_kreise, wohnort)

        slope = context.config("gravity_slope")

        df_work_matrix = build_pendler_constrained_matrix(
            municipalities,
            df_employees.reset_index() if "destination_id" not in df_employees.columns else df_employees,
            df_distances.reset_index() if "origin_id" not in df_distances.columns else df_distances,
            pendler_shares, study_kreise, slope
        )

        # Compute outside fraction per municipality (from Kreis-level Pendler)
        outside_by_kreis = {}
        for _, row in pendler_shares.iterrows():
            if row["destination_kreis"] == "_outside":
                outside_by_kreis[row["origin_kreis"]] = row["share"]

        df_outside = pd.DataFrame([
            {"commune_id": mun, "outside_fraction": outside_by_kreis.get(mun[:5], 0.0)}
            for mun in municipalities
        ])

        n_affected = (df_outside["outside_fraction"] > 0).sum()
        print(f"Outside commuter fractions: {n_affected} municipalities affected, "
              f"range {df_outside['outside_fraction'].min():.1%}-{df_outside['outside_fraction'].max():.1%}")

        # Education: pure gravity (no Pendler data for education)
        df_education_matrix = _build_pure_gravity(
            context, municipalities, df_population, df_employees, df_distances
        )

        return df_work_matrix, df_education_matrix, df_outside

    else:
        # === PURE GRAVITY MODE (backward compatible) ===
        df_matrix = _build_pure_gravity(
            context, municipalities, df_population, df_employees, df_distances
        )
        return df_matrix, df_matrix


def _build_pure_gravity(context, municipalities, df_population, df_employees, df_distances):
    """Original gravity model logic, extracted to a helper."""
    # Make sure we have all municipalities in all data sets
    df_population = df_population.set_index("origin_id").reindex(municipalities).fillna(0.0)
    df_employees = df_employees.set_index("destination_id").reindex(municipalities).fillna(0.0) if "destination_id" in df_employees.columns else df_employees.reindex(municipalities).fillna(0.0)
    df_distances = df_distances.set_index(["origin_id", "destination_id"]).reindex(pd.MultiIndex.from_product([
        municipalities, municipalities
    ])) if "origin_id" in df_distances.columns else df_distances.reindex(pd.MultiIndex.from_product([
        municipalities, municipalities
    ]))

    # Transform from a list into a matrix
    distances = df_distances["distance_km"].values.reshape((len(municipalities), len(municipalities)))

    # Run model
    population = df_population["population"]
    employees = df_employees["employees"]

    # Balancing of the remaining population and workplaces
    observations = min(np.sum(population), np.sum(employees))
    population *= observations / np.sum(population)
    employees *= observations / np.sum(employees)

    # Model parameters estimated from Île-de-France
    slope = context.config("gravity_slope")
    constant = context.config("gravity_constant")
    diagonal = context.config("gravity_diagonal")

    friction = np.exp(slope * distances + constant) + np.eye(len(municipalities)) * diagonal
    flow = evaluate_gravity(population, employees, friction)

    # Convert to data frame
    df_matrix = pd.DataFrame({
        "weight": flow.reshape((-1,)),
    }, index = pd.MultiIndex.from_product([municipalities, municipalities], names = [
        "origin_id", "destination_id"
    ])).reset_index()

    # Calculate totals
    df_total = df_matrix[["origin_id", "weight"]].groupby("origin_id").sum().reset_index().rename({ "weight" : "total" }, axis = 1)
    df_matrix = pd.merge(df_matrix, df_total, on = "origin_id")

    # Fix missing flows
    f_missing_total = df_matrix["total"] == 0.0
    df_matrix.loc[f_missing_total & (df_matrix["origin_id"] == df_matrix["destination_id"]), "weight"] = 1.0
    df_matrix.loc[f_missing_total, "total"] = 1.0

    # Convert to probability
    df_matrix["weight"] = df_matrix["weight"] / df_matrix["total"]
    df_matrix = df_matrix[["origin_id", "destination_id", "weight"]]

    return df_matrix
