# Using UV with Bavaria Scenario

This project has been configured to use **UV** (by Astral) as the package manager, which is 10-100x faster than conda.

## Quick Start

```bash
# Install dependencies (replaces: conda env create -f environment.yml)
uv sync

# Run any Python script (replaces: conda activate bavaria && python script.py)
uv run python synthesis/population/spatial/home_locations.py

# Run tests
uv run pytest

# Run the main pipeline
uv run python run_batch.py
```

## Why UV?

- **10-100x faster** than conda/poetry for dependency resolution
- **No environment activation** needed - just use `uv run`
- **Better reproducibility** with lock files
- **Consistent** with ExMasCommuter project

## Required External Tools

### 1. OSMConvert ✅ (Already configured!)
- **Status:** You've already added `osmconvert.exe` to `tools/`
- **No action needed** - it's already referenced in `config_bavaria.yml`

### 2. Osmosis ⚠️ (Manual download required!)

**Osmosis is NOT downloaded automatically** - you need to download it manually.

#### Download Instructions:

1. **Download Osmosis**
   - Go to: https://github.com/openstreetmap/osmosis/releases
   - Download the latest version (e.g., `osmosis-0.49.2.zip`)
   - Alternative: https://wiki.openstreetmap.org/wiki/Osmosis

2. **Extract to tools directory**
   ```bash
   # Extract the zip file to:
   tools/osmosis/
   
   # Your directory structure should look like:
   # bavaria/
   #   tools/
   #     osmconvert.exe  ✅ (already done!)
   #     osmosis/
   #       bin/
   #         osmosis      (Linux/Mac)
   #         osmosis.bat  (Windows)
   #       lib/
   #       ...
   ```

3. **Verify installation**
   ```bash
   # Windows
   tools\osmosis\bin\osmosis.bat -v
   
   # Linux/Mac
   tools/osmosis/bin/osmosis -v
   ```

4. **Update config if needed**
   - The path is already set in `config_bavaria.yml`:
     ```yaml
     osmosis_binary: tools/osmosis/bin/osmosis
     ```
   - On Windows, you might need to change it to:
     ```yaml
     osmosis_binary: tools/osmosis/bin/osmosis.bat
     ```

### 3. Java (Required for Osmosis)
Osmosis requires Java 8 or later. Install from:
- **Windows:** https://adoptium.net/ or `winget install EclipseAdoptium.Temurin.21.JDK`
- **Linux:** `sudo apt install openjdk-21-jdk`
- **Mac:** `brew install openjdk@21`

## Commands Comparison

| Task | Conda (old) | UV (new) |
|------|-------------|----------|
| Install deps | `conda env create -f environment.yml` | `uv sync` |
| Activate env | `conda activate bavaria` | *(not needed)* |
| Run script | `python script.py` | `uv run python script.py` |
| Run tests | `pytest` | `uv run pytest` |
| Add package | `conda install package` | `uv add package` |
| Remove package | `conda remove package` | `uv remove package` |

## Migrating from Conda

If you have an existing conda environment:

```bash
# Deactivate conda (optional)
conda deactivate

# Install UV (if not already installed)
# Windows PowerShell:
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# Initialize UV environment
cd bavaria
uv sync

# You can now delete the conda environment if desired
conda env remove -n bavaria
```

## Troubleshooting

### "osmosis: command not found"
- Download osmosis manually (see instructions above)
- Update the path in `config_bavaria.yml`

### "Java not found" error from osmosis
- Install Java 8+ (see instructions above)
- Verify: `java -version`

### UV not found
```bash
# Windows PowerShell (admin not required):
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# Linux/Mac:
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Files Created for UV

- `pyproject.toml` - Project metadata and dependencies (replaces environment.yml)
- `.python-version` - Python version specification (optional)
- `uv.lock` - Lock file for reproducible installs (auto-generated)

## Notes

- The `environment.yml` and `requirements.txt` are kept for backward compatibility
- UV is faster because it's written in Rust and uses better algorithms
- Lock files ensure everyone gets the exact same dependency versions
