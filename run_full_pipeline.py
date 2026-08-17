"""
run_full_pipeline.py
====================
Assam Flood Digital Twin - Prototype Pipeline Orchestrator

This script runs the complete flood early warning and safe route pipeline
in sequential order. It calls each phase via subprocess, monitors for errors,
and strictly verifies that the expected output files are generated.

Features:
- Robust error handling (subprocess.CalledProcessError)
- Timestamps and logging for each phase
- File existence verification with [PASS] / [FAIL]
- UTF-8 console output support for Windows environments
"""

import os
import sys
import time
import subprocess
from datetime import datetime

# Configure Windows console to handle UTF-8 characters properly
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Define the pipeline phases mapped to the actual scripts and expected outputs in our directory
PHASES = [
    {
        "name": "Phase 1: Data Acquisition",
        "script": "download_dem_rivers.py",
        "expected_outputs": ["dem.tif", "rivers.shp"]
    },
    {
        "name": "Phase 2: Hydrological Modeling (Runoff)",
        "script": "rainfall_runoff.py",
        "expected_outputs": ["discharge_2020.csv", "hydrograph_2020.png"]
    },
    {
        "name": "Phase 3: Flood Inundation (HAND)",
        "script": "flood_hand.py",
        "expected_outputs": ["flood_depth.tif", "flood_map.png"]
    },
    {
        "name": "Phase 4: Sensor Data Assimilation",
        "script": "sensor_assimilation.py",
        # Simulates real-time sensor loop; outputs result to CSV and PNG
        "expected_outputs": ["assimilation_result.csv", "assimilation_final.png"]
    },
    {
        "name": "Phase 5: Safe Evacuation Routing",
        "script": "flood_routing.py",
        "expected_outputs": ["evacuation_route.geojson", "evacuation_report.txt"]
    },
    {
        "name": "Phase 6: Dashboard Data Preparation",
        "script": "prepare_webapp_data.py",
        "expected_outputs": [
            os.path.join("webapp", "data", "flood_extent.geojson"),
            os.path.join("webapp", "data", "safe_route.geojson")
        ]
    },
    {
        "name": "Phase 7: Dynamic Digital Twin Data Preparation",
        "script": "prepare_dynamic_data.py",
        "expected_outputs": [
            os.path.join("webapp", "data", "dynamic_data.json")
        ]
    }
]

def log(message):
    """Simple logging format with timestamps."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")

def check_outputs(expected_outputs):
    """Check that the expected output files exist."""
    all_passed = True
    for file_path in expected_outputs:
        if os.path.exists(file_path):
            print(f"  [PASS] Found output: {file_path}")
        else:
            print(f"  [FAIL] Missing expected output: {file_path}")
            all_passed = False
    return all_passed

def run_phase(phase):
    """Run a single phase via subprocess and verify its outputs."""
    log(f"Starting {phase['name']} ({phase['script']})...")
    
    script_path = phase['script']
    if not os.path.exists(script_path):
        log(f"[FATAL] Script {script_path} not found in the current directory.")
        return False
        
    start_time = time.time()
    
    try:
        # Run the script using the current Python executable
        result = subprocess.run(
            [sys.executable, script_path, "--headless"],
            check=True,
            text=True
        )
    except subprocess.CalledProcessError as e:
        log(f"[ERROR] {phase['name']} failed with exit code {e.returncode}.")
        return False
    except FileNotFoundError:
        log(f"[FATAL] Could not execute {sys.executable}. Python might not be correctly installed.")
        return False

    elapsed = time.time() - start_time
    log(f"{phase['name']} finished in {elapsed:.1f} seconds.")
    
    # Verify outputs
    log("Verifying outputs...")
    passed = check_outputs(phase["expected_outputs"])
    if not passed:
        log(f"[WARNING] {phase['name']} completed, but some expected outputs are missing.")
    
    return passed

def main():
    print("=" * 70)
    print("Assam Flood Digital Twin - Prototype Pipeline")
    print("=" * 70)
    print("This script will execute all phases of the early warning system.\n")
    
    global_start = time.time()
    failed = False
    
    for phase in PHASES:
        print("-" * 70)
        success = run_phase(phase)
        if not success:
            log(f"[ABORT] Halting pipeline due to failure in {phase['name']}.")
            failed = True
            break
            
    print("=" * 70)
    total_elapsed = time.time() - global_start
    log(f"Pipeline execution finished. Total runtime: {total_elapsed / 60:.2f} minutes.")
    
    if not failed:
        print("\nAll phases completed successfully.")
        print("Open webapp/index.html in your browser (or run 'cd webapp && python -m http.server 8080')")
        print("to see the live flood map and safe evacuation route.")
        sys.exit(0)
    else:
        print("\nPipeline terminated prematurely due to errors.")
        sys.exit(1)

if __name__ == "__main__":
    main()
