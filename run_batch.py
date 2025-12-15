import yaml
import os
import subprocess
import shutil
from pathlib import Path

# Configuration
SCENARIOS = [
    {"rate": 0.01, "prefix": "bavaria_1pct_"},
    {"rate": 0.10, "prefix": "bavaria_10pct_"},
    {"rate": 0.25, "prefix": "bavaria_25pct_"},
    {"rate": 1.00, "prefix": "bavaria_100pct_"},
]

CONFIG_FILE = "config_bavaria.yml"
BACKUP_FILE = "config_bavaria.yml.bak"

def run_command(command):
    process = subprocess.Popen(command, shell=True)
    process.wait()
    if process.returncode != 0:
        raise Exception(f"Command failed: {command}")

def main():
    # Backup original config
    if not os.path.exists(BACKUP_FILE):
        shutil.copy(CONFIG_FILE, BACKUP_FILE)

    try:
        # 1. Ensure Data is Downloaded
        print("--- Checking Data ---")
        if not os.path.exists("data/osm/bayern-latest.osm.pbf"):
            print("Data not found. Running download script...")
            run_command("python3 scripts/download_data.py")

        # 2. Run Scenarios
        for scenario in SCENARIOS:
            rate = scenario["rate"]
            prefix = scenario["prefix"]
            
            print(f"\n\n=== STARTING SCENARIO: {rate*100}% (Prefix: {prefix}) ===")
            
            # Read config
            with open(BACKUP_FILE, 'r') as f:
                config = yaml.safe_load(f)
            
            # Modify config
            config['config']['sampling_rate'] = rate
            config['config']['output_prefix'] = prefix
            
            # Ensure we are using the correct political prefix (Oberbayern)
            # You can change this to ["09"] for all of Bavaria if you have the RAM
            config['config']['bavaria.political_prefix'] = ["091"] 
            
            # Write modified config
            with open(CONFIG_FILE, 'w') as f:
                yaml.dump(config, f)
            
            # Run Pipeline
            print(f"Running pipeline for {rate*100}%...")
            run_command("python3 -m synpp config_bavaria.yml")
            
            # Organize Output
            output_dir = Path("output") / f"scenario_{int(rate*100)}pct"
            output_dir.mkdir(parents=True, exist_ok=True)
            
            print(f"Moving results to {output_dir}...")
            # Move specific output files to the scenario folder
            # We look for files starting with the prefix
            for file in Path("output").glob(f"{prefix}*"):
                shutil.move(str(file), str(output_dir / file.name))
                
        print("\n\n=== ALL SCENARIOS COMPLETED SUCCESSFULLY ===")

    except Exception as e:
        print(f"\n\nERROR: {e}")
    finally:
        # Restore original config
        if os.path.exists(BACKUP_FILE):
            shutil.move(BACKUP_FILE, CONFIG_FILE)

if __name__ == "__main__":
    main()
