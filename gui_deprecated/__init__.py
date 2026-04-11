# PawFlow GUI

"""
Interface graphique Streamlit pour PawFlow.
Permet de créer, déployer et monitorer des pipelines de données.
"""

import sys
from pathlib import Path

# Ensure project root is ALWAYS in sys.path — this runs on any import of gui.*
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

__version__ = "0.1.0"
__author__ = "PawFlow Team"