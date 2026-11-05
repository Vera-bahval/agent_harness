"""Trusted course-task checker; never copied into fixture."""
from pathlib import Path
import runpy
import sys
runpy.run_path(str(Path(__file__).resolve().parents[2]/'checks.py'))['main']('B1.2', Path(sys.argv[1]), Path(__file__).resolve().parent/'fixture')
