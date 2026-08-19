import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "tests" / "js" / "pfp_depot_spec.js"


@pytest.mark.skipif(shutil.which("node") is None,
                    reason="node is not available to run the JS suite")
def test_pfp_depot_hides_install_for_installed_version():
    proc = subprocess.run(
        ["node", str(SPEC)], capture_output=True, text=True,
        cwd=str(ROOT), timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "pfp depot spec: ok" in proc.stdout
