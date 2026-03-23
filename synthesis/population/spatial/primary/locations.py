import numpy as np
import pandas as pd
import geopandas as gpd
import logging
from .candidates import EDUCATION_MAPPING

logger = logging.getLogger(__name__)

def configure(context):
    context.stage("synthesis.population.spatial.primary.candidates")
    context.stage("synthesis.population.spatial.commute_distance")
    context.stage("synthesis.population.spatial.home.locations")
    context.stage("synthesis.locations.work")
    context.stage("synthesis.locations.education")

    context.config("education_location_source", "bpe")

    # If True, allow multiple persons to be assigned to the same facility,
    # weighted by employee count. Each person picks the facility whose distance
    # from home best matches their target commute distance.
    # If False, use legacy 1:1 exclusive assignment.
    context.config("shared_facility_assignment", True)


def assign_shared_facilities(df_persons, df_candidates, df_facility_weights, progress):
    """Assign each person to the candidate facility that best matches their
    target commute distance. Facilities can be shared by multiple persons,
    with a soft capacity proportional to employee count."""

    # Build unique facility list from candidates
    unique_facilities = df_candidates.drop_duplicates("location_id").copy()
    fac_coords = np.vstack([
        unique_facilities["geometry"].x.values,
        unique_facilities["geometry"].y.values
    ]).T
    fac_location_ids = unique_facilities["location_id"].values
    fac_dest_ids = unique_facilities["destination_id"].values
    fac_geoms = unique_facilities["geometry"].values

    # Get employee weights for capacity-aware sampling
    weight_map = {}
    if df_facility_weights is not None and "employees" in df_facility_weights.columns:
        for _, row in df_facility_weights.iterrows():
            weight_map[row["location_id"]] = max(row["employees"], 1)

    fac_capacities = np.array([weight_map.get(lid, 1) for lid in fac_location_ids], dtype=float)
    fac_usage = np.zeros(len(fac_location_ids), dtype=float)

    results = []

    for person_id, home_loc, commute_dist in zip(
        df_persons["person_id"].values,
        df_persons["home_location"].values,
        df_persons["commute_distance"].values
    ):
        home_xy = np.array([home_loc.x, home_loc.y])
        distances = np.sqrt(np.sum((fac_coords - home_xy) ** 2, axis=1))

        # Cost = |actual_distance - target_distance|
        # Add a soft overcapacity penalty: small penalty when usage exceeds capacity
        distance_cost = np.abs(distances - commute_dist)
        overcapacity = np.maximum(0, fac_usage - fac_capacities) / np.maximum(fac_capacities, 1)
        cost = distance_cost + overcapacity * commute_dist * 0.1  # 10% penalty per overcapacity unit

        best_idx = np.argmin(cost)
        fac_usage[best_idx] += 1

        results.append({
            "person_id": person_id,
            "commune_id": fac_dest_ids[best_idx],
            "location_id": fac_location_ids[best_idx],
            "geometry": fac_geoms[best_idx],
        })

        progress.update()

    return pd.DataFrame(results)


def define_distance_ordering(df_persons, df_candidates, progress):
    """Legacy 1:1 exclusive assignment."""
    indices = []

    f_available = np.ones((len(df_candidates),), dtype = bool)
    costs = np.ones((len(df_candidates),)) * np.inf

    commute_coordinates = np.vstack([
        df_candidates["geometry"].x.values,
        df_candidates["geometry"].y.values
    ]).T

    for home_coordinate, commute_distance in zip(df_persons["home_location"], df_persons["commute_distance"]):
        home_coordinate = np.array([home_coordinate.x, home_coordinate.y])
        distances = np.sqrt(np.sum((commute_coordinates[f_available] - home_coordinate)**2, axis = 1))
        costs[f_available] = np.abs(distances - commute_distance)

        selected_index = np.argmin(costs)
        indices.append(selected_index)
        f_available[selected_index] = False
        costs[selected_index] = np.inf

        progress.update()

    assert len(set(indices)) == len(df_candidates)

    return indices


def process_municipality_shared(context, origin_id):
    df_candidates = context.data("df_candidates")
    df_persons = context.data("df_persons")
    df_facility_weights = context.data("df_facility_weights")

    df_persons = df_persons[df_persons["commune_id"] == origin_id][[
        "person_id", "home_location", "commute_distance"
    ]].copy()
    df_candidates = df_candidates[df_candidates["origin_id"] == origin_id]

    if len(df_persons) == 0:
        return pd.DataFrame(columns=["person_id", "commune_id", "location_id", "geometry"])

    result = assign_shared_facilities(df_persons, df_candidates, df_facility_weights, context.progress)
    return result


def process_municipality_legacy(context, origin_id):
    df_candidates, df_persons = context.data("df_candidates"), context.data("df_persons")

    df_persons = df_persons[df_persons["commune_id"] == origin_id][[
        "person_id", "home_location", "commute_distance"
    ]].copy()
    df_candidates = df_candidates[df_candidates["origin_id"] == origin_id]

    assert len(df_persons) == len(df_candidates)

    indices = define_distance_ordering(df_persons, df_candidates, context.progress)
    df_candidates = df_candidates.iloc[indices]

    df_candidates["person_id"] = df_persons["person_id"].values
    df_candidates = df_candidates.rename(columns = dict(destination_id = "commune_id"))

    return df_candidates[["person_id", "commune_id", "location_id", "geometry"]]


def process(context, purpose, df_persons, df_candidates, df_facility_weights=None):
    unique_ids = df_candidates["origin_id"].unique()
    shared = context.config("shared_facility_assignment")

    if shared:
        logger.info("Using shared facility assignment for %s (distance-based, employee-weighted)", purpose)
        process_fn = process_municipality_shared
        parallel_data = dict(df_persons=df_persons, df_candidates=df_candidates, df_facility_weights=df_facility_weights)
    else:
        process_fn = process_municipality_legacy
        parallel_data = dict(df_persons=df_persons, df_candidates=df_candidates)

    df_result = []

    with context.progress(label = "Distributing %s destinations" % purpose, total = len(df_persons)) as progress:
        with context.parallel(parallel_data) as parallel:
            for df_partial in parallel.imap_unordered(process_fn, unique_ids):
                df_result.append(df_partial)

    return pd.concat(df_result).sort_index()

def execute(context):
    data = context.stage("synthesis.population.spatial.primary.candidates")
    df_persons = data["persons"]

    # Separate data set
    df_work = df_persons[df_persons["has_work_trip"]]
    df_education = df_persons[df_persons["has_education_trip"]]

    # Attach home locations
    df_home = context.stage("synthesis.population.spatial.home.locations")

    df_work = pd.merge(df_work, df_home[["household_id", "geometry"]].rename(columns = {
        "geometry": "home_location"
    }), how = "left", on = "household_id")

    df_education = pd.merge(df_education, df_home[["household_id", "geometry"]].rename(columns = {
        "geometry": "home_location"
    }), how = "left", on = "household_id")

    # Attach commute distances
    df_commute_distance = context.stage("synthesis.population.spatial.commute_distance")

    df_work = pd.merge(df_work, df_commute_distance["work"], how = "left", on = "person_id")
    df_education = pd.merge(df_education, df_commute_distance["education"], how = "left", on = "person_id")

    # Attach geometry
    df_locations = context.stage("synthesis.locations.work")[["location_id", "geometry"]]
    df_work_candidates = data["work_candidates"]
    df_work_candidates = pd.merge(df_work_candidates, df_locations, how = "left", on = "location_id")
    df_work_candidates = gpd.GeoDataFrame(df_work_candidates)

    df_locations = context.stage("synthesis.locations.education")[["education_type", "location_id", "geometry"]]
    df_education_candidates = data["education_candidates"]
    df_education_candidates = pd.merge(df_education_candidates, df_locations, how = "left", on = "location_id")
    df_education_candidates = gpd.GeoDataFrame(df_education_candidates)

    # Facility weights for shared assignment
    df_work_weights = context.stage("synthesis.locations.work")[["location_id", "employees"]]
    df_edu_weights = None  # education facilities don't have employee counts

    # Assign destinations
    df_work = process(context, "work", df_work, df_work_candidates, df_work_weights)
    if context.config("education_location_source") == 'bpe':
        df_education = process(context, "education", df_education, df_education_candidates, df_edu_weights)
    else :
        education = []
        for prefix, education_type in EDUCATION_MAPPING.items():
            education.append(process(context, prefix,df_education[df_education["age_range"]==prefix],df_education_candidates[df_education_candidates["education_type"].isin(education_type)], df_edu_weights))
        df_education = pd.concat(education).sort_index()
    return df_work, df_education
