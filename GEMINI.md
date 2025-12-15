# Bavaria Synthetic Population Pipeline

## Project Overview
This project generates an open synthetic population for Bavaria, Germany. It uses open data to create a dataset representing socio-demographic attributes and daily mobility patterns (activities and trips) of the population. This data is primarily used for agent-based transport simulations (specifically **MATSim**) but also for other research applications like disease spreading or service placement.

The pipeline is built on the `synpp` (Synthetic Population Pipeline) framework and is an adaptation of a methodology originally developed for Île-de-France (Paris).

## Key Technologies
*   **Language:** Python 3.10+
*   **Pipeline Framework:** `synpp`
*   **Data Handling:** `pandas`, `geopandas`, `shapely`, `numpy`, `pytables`
*   **Simulation Output:** MATSim (XML/Java)
*   **Testing:** `pytest`

## Architecture & Directory Structure
*   **`bavaria/`**: Contains the core logic specific to the Bavaria implementation (zones, income, homes, locations).
*   **`config_bavaria.yml`**: The main configuration file for running the Bavaria pipeline.
*   **`data/`**: Modules for handling raw data inputs (Census, GTFS, OSM, HTS).
*   **`synthesis/`**: Generic population synthesis logic.
*   **`matsim/`**: Modules for converting synthetic population data into MATSim input formats.
*   **`docs/`**: Documentation for data gathering and simulation setup.
*   **`tests/`**: Unit and integration tests.

## Setup & usage

### 1. Environment Setup
The project uses Conda for dependency management.
```bash
conda env create -f environment.yml -n bavaria
conda activate bavaria
```

### 2. Data Gathering
**Crucial:** The pipeline requires specific raw data files to be placed in a `data/` directory. Refer to `docs/population.md` for a detailed list of required files (administrative boundaries, population data, employment data, etc.) and where to download them.

### 3. Configuration
Edit `config_bavaria.yml` to set paths:
*   `working_directory`: Path for temporary/cache files.
*   `data_path`: Path to the raw data directory.
*   `output_path`: Path where results will be saved.

### 4. Running the Pipeline
Execute the pipeline using the `synpp` module:
```bash
python3 -m synpp config_bavaria.yml
```
This will generate `persons.csv`, `households.csv`, `activities.csv`, `trips.csv`, and corresponding GeoPackage files in the output directory.

### 5. Running Tests
Run tests using `pytest`:
```bash
pytest
```

## Development Conventions
*   **Pipeline Structure:** Functionality is divided into stages (defined in `config.yml`). New data sources or logic should be integrated as new pipeline stages.
*   **Data Flow:** Raw data -> Cleaned Data -> Synthesis -> Output.
*   **Code Style:** Follow standard Python PEP 8 conventions.
