import subprocess as sp
import shutil
import os

"""
The purpose of this stage is to cut the OSM data into smaller chunks so we can process
it more easily later on.

Uses osmium-tool (osmium extract) instead of osmconvert for better stability with large
PBF files on Windows. Install via: micromamba install -n bavaria osmium-tool -c conda-forge
"""

def configure(context):
    context.stage("data.spatial.municipalities")

    context.config("processes")

    context.config("data_path")
    context.config("osm_path_bavaria", "osm/bayern-latest.osm.pbf")
    context.config("osmium_binary", "osmium")

def _run_osmium_extract(context, input_path, poly_path, output_path, cwd=None):
    """Extract OSM data for a polygon using osmium extract."""
    binary = shutil.which(context.config("osmium_binary"))
    if binary is None:
        raise RuntimeError("Cannot find osmium binary. Install osmium-tool via: micromamba install -n bavaria osmium-tool -c conda-forge")

    command_line = [
        binary, "extract",
        "-p", poly_path,
        "-o", output_path,
        "--overwrite",
        input_path
    ]

    return_code = sp.check_call(command_line, cwd=cwd)
    if return_code != 0:
        raise RuntimeError("osmium extract return code: %d" % return_code)

def process_municipality(context, zone_id):
    input_path = context.data("input_path")
    local_path = context.data("local_path")

    poly_path = "{}/{}.poly".format(local_path, zone_id)
    output_path = "{}/{}.osm.pbf".format(local_path, zone_id)

    _run_osmium_extract(context, input_path, poly_path, output_path, cwd=local_path)

    return zone_id
    
def execute(context):
    # Load zones and convert to polyfiles
    df_zones = context.stage("data.spatial.municipalities")[["commune_id", "geometry"]]
    df_zones = df_zones.to_crs("EPSG:4326")

    for zone_id, geometry in df_zones.itertuples(index = False):
        if not hasattr(geometry, "exterior"):
            geometry = geometry.convex_hull

        data = []
        data.append("polyfile")
        data.append("polygon")

        for coordinate in geometry.exterior.coords:
            data.append("    %e    %e" % coordinate[:2])

        data.append("END")
        data.append("END")

        with open("{}/{}.poly".format(context.path(), zone_id), "w+") as f:
            f.write("\n".join(data))
    
    # Cut into chunks
    with context.progress(label = "Chunking OSM data ...", total = len(df_zones)) as progress:
        with context.parallel({
            "input_path": "{}/{}".format(context.config("data_path"), context.config("osm_path_bavaria")),
            "local_path": context.path()
        }) as parallel:
            for item in parallel.imap(process_municipality, df_zones["commune_id"].values):
                progress.update()

    return df_zones["commune_id"].values

def validate(context):
    return os.path.getsize("{}/{}".format(context.config("data_path"), context.config("osm_path_bavaria")))