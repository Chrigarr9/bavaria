import pandas as pd
import logging

logger = logging.getLogger(__name__)

def configure(context):
    context.stage("synthesis.population.enriched")
    context.stage("data.hts.commute_distance")

    # Distance scaling factors to adjust HTS-derived commute distances
    # to match regional target distributions (e.g. MiD 2017 Bayern).
    # A factor of 1.0 means no scaling (use HTS distances as-is).
    context.config("commute_distance_scale_work", 1.0)
    context.config("commute_distance_scale_education", 1.0)

def execute(context):
    df_matching = context.stage("synthesis.population.enriched")
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

    return dict(
        work = df_work, education = df_education
    )
