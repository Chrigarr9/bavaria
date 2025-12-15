import os
import requests
from pathlib import Path

# --- CONFIGURATION ---
# Paste your download links inside the quotes for each file.
# If a link is missing (empty string), that file will be skipped with a warning.

DATA_FILES = {
    # 1) German administrative boundaries
    "data/germany/vg250-ew_12-31.utm32s.gpkg.ebenen.zip": "https://daten.gdz.bkg.bund.de/produkte/vg/vg250-ew_ebenen_1231/aktuell/vg250-ew_12-31.utm32s.gpkg.ebenen.zip",

    # 2) Bavarian population data
    "data/bavaria/a1310c_202200.xla": "https://www.statistik.bayern.de/mam/produkte/veroffentlichungen/statistische_berichte/a1310c_202200.xlsx",

    # 3) Bavarian employment data (district)
    "data/bavaria/13111-004r.xlsx": "https://www.statistikdaten.bayern.de/genesis/online?operation=ergebnistabelleDownload&levelindex=1&levelid=1765176803409&option=xlsx",

    # 4) Bavarian employment data (municipality)
    "data/bavaria/a6502c_202200.xla": "https://www.statistik.bayern.de/mam/produkte/veroffentlichungen/statistische_berichte/a6502c_202200.xlsx",

    # 5) Bavarian household size data
    "data/bavaria/12211-105.xlsx": "https://www.statistikdaten.bayern.de/genesis/online?operation=ergebnistabelleDownload&levelindex=3&levelid=1765177036899&option=xlsx",

    # 6) Bavarian household income data
    "data/bavaria/12211-101.xlsx": "https://www.statistikdaten.bayern.de/genesis/online?operation=ergebnistabelleDownload&levelindex=3&levelid=1765177178270&option=xlsx",

    # 7) German driving license ownership data
    "data/germany/fe4_2024.xlsx": "https://www.kba.de/SharedDocs/Downloads/DE/Statistik/Kraftfahrer/FE4/fe4_2024.xlsx?__blob=publicationFile&v=2",

    # 8) Bavarian building registry (7 regions)
    "data/bavaria/buildings/091_Oberbayern_Hausumringe.zip": "https://geodaten.bayern.de/odd/m/3/daten/hausumringe/bezirk/data/091_Oberbayern_Hausumringe.zip",
    "data/bavaria/buildings/092_Niederbayern_Hausumringe.zip": "https://geodaten.bayern.de/odd/m/3/daten/hausumringe/bezirk/data/092_Niederbayern_Hausumringe.zip",
    "data/bavaria/buildings/093_Oberpfalz_Hausumringe.zip": "https://geodaten.bayern.de/odd/m/3/daten/hausumringe/bezirk/data/093_Oberpfalz_Hausumringe.zip",
    "data/bavaria/buildings/094_Oberfranken_Hausumringe.zip": "https://geodaten.bayern.de/odd/m/3/daten/hausumringe/bezirk/data/094_Oberfranken_Hausumringe.zip",
    "data/bavaria/buildings/095_Mittelfranken_Hausumringe.zip": "https://geodaten.bayern.de/odd/m/3/daten/hausumringe/bezirk/data/095_Mittelfranken_Hausumringe.zip",
    "data/bavaria/buildings/096_Unterfranken_Hausumringe.zip": "https://geodaten.bayern.de/odd/m/3/daten/hausumringe/bezirk/data/096_Unterfranken_Hausumringe.zip",
    "data/bavaria/buildings/097_Schwaben_Hausumringe.zip": "https://geodaten.bayern.de/odd/m/3/daten/hausumringe/bezirk/data/097_Schwaben_Hausumringe.zip",

    # 9) French National household travel survey (ENTD 2008)
    "data/entd_2008/Q_tcm_menage_0.csv": "https://www.statistiques.developpement-durable.gouv.fr/media/2339/download?inline",
    "data/entd_2008/Q_tcm_individu.csv": "https://www.statistiques.developpement-durable.gouv.fr/media/2555/download?inline",
    "data/entd_2008/Q_menage.csv": "https://www.statistiques.developpement-durable.gouv.fr/media/2556/download?inline",
    "data/entd_2008/Q_individu.csv": "https://www.statistiques.developpement-durable.gouv.fr/media/2565/download?inline",
    "data/entd_2008/Q_ind_lieu_teg.csv": "https://www.statistiques.developpement-durable.gouv.fr/media/2566/download?inline",
    "data/entd_2008/K_deploc.csv": "https://www.statistiques.developpement-durable.gouv.fr/media/2568/download?inline",

    # 10) German GTFS
    "data/gtfs/latest.zip": "https://download.gtfs.de/germany/free/latest.zip",

    # 11) OpenStreetMap (Bavaria)
    "data/osm/bayern-latest.osm.pbf": "https://download.geofabrik.de/europe/germany/bayern-251207.osm.pbf",

    # 12) MVG Zoning system
    "data/mvg/stations.json": "https://www.mvg.de/.rest/zdm/stations",
}

def download_file(url, filepath):
    """Downloads a file from a URL to a specific path, creating directories if needed."""
    if not url:
        print(f"SKIPPING: {filepath} (No URL provided)")
        return

    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Downloading {filepath}...")
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()
        with open(path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        print(f"SUCCESS: {filepath}")
    except Exception as e:
        print(f"ERROR downloading {filepath}: {e}")

if __name__ == "__main__":
    print("Starting data download...")
    print("Note: Ensure you have pasted the download links into the script source code.")
    
    # Base directory assumed to be the project root where this script is run, 
    # or adjust relative to the script location.
    # Assuming script is run from project root:
    base_dir = Path(".")
    
    for relative_path, url in DATA_FILES.items():
        full_path = base_dir / relative_path
        download_file(url, full_path)
    
    print("\nDownload process completed.")