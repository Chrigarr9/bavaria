import runpy, sys, os
os.chdir(r"C:\Users\VWAUCCY\dev\msf\projects\Dissertation\matsim_scenarios\bavaria")
sys.path.insert(0, ".")
runpy.run_path("scripts/compare_commuter_od.py")
