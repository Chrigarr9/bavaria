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
