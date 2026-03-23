import numpy as np
import pandas as pd
import logging

logger = logging.getLogger(__name__)

def configure(context):
    context.stage("data.hts.selected", alias = "hts")

    # Distance scaling factor for secondary activity locations (shop, leisure, errands, etc.)
    # Scales the HTS-derived distance distributions to match regional targets.
    # Can be a single float (applied to all modes) or left at 1.0 for no scaling.
    context.config("secondary_distance_scale", 1.0)

def calculate_bounds(values, bin_size):
    values = np.sort(values)

    bounds = []
    current_count = 0
    previous_bound = None

    for value in values:
        if value == previous_bound:
            continue

        if current_count < bin_size:
            current_count += 1
        else:
            current_count = 0
            bounds.append(value)
            previous_bound = value

    if len(bounds) > 0:
        bounds[-1] = np.inf
    else:
        bounds.append(np.inf)
        
    return bounds

def execute(context):
    # Prepare data
    df_households, df_persons, df_trips = context.stage("hts")

    secondary_distance_scale = context.config("secondary_distance_scale")

    df_trips = pd.merge(df_trips, df_persons[["person_id", "person_weight"]].rename(columns = { "person_weight": "weight" }))
    df_trips["travel_time"] = df_trips["arrival_time"] - df_trips["departure_time"]

    distance_column = "euclidean_distance" if "euclidean_distance" in df_trips else "routed_distance"
    df = df_trips[["mode", "travel_time", distance_column, "weight", "preceding_purpose", "following_purpose"]].rename(columns = { distance_column: "distance" })

    if secondary_distance_scale != 1.0:
        logger.info("Scaling secondary activity distances by factor %.2f", secondary_distance_scale)
        df["distance"] = df["distance"] * secondary_distance_scale

    # Filtering
    primary_activities = ["home", "work", "education"]
    df = df[~(
        df["preceding_purpose"].isin(primary_activities) &
        df["following_purpose"].isin(primary_activities)
    )]

    # Calculate distributions
    modes = df["mode"].unique()

    bin_size = 200
    distributions = {}

    for mode in modes:
        # First calculate bounds by unique values
        f_mode = df["mode"] == mode
        bounds = calculate_bounds(df[f_mode]["travel_time"].values, bin_size)

        distributions[mode] = dict(bounds = np.array(bounds), distributions = [])

        # Second, calculate distribution per band
        for lower_bound, upper_bound in zip([-np.inf] + bounds[:-1], bounds):
            f_bound = (df["travel_time"] > lower_bound) & (df["travel_time"] <= upper_bound)

            # Set up distribution
            values = df[f_mode & f_bound]["distance"].values
            weights = df[f_mode & f_bound]["weight"].values

            sorter = np.argsort(values)
            values = values[sorter]
            weights = weights[sorter]

            cdf = np.cumsum(weights)
            cdf /= cdf[-1]

            # Write distribution
            distributions[mode]["distributions"].append(dict(cdf = cdf, values = values, weights = weights))

    return distributions
