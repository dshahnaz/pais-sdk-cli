"""`pais-dev` console script was removed in v0.5.0. The Python module shim
remains so `python -m pais.cli.dev` from a stale install prints a useful
redirect instead of crashing."""

from __future__ import annotations

import subprocess
import sys


def test_pais_dev_shim_exits_with_redirect_message() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "pais.cli.dev"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1, proc.stderr
    assert "pais-dev was removed" in proc.stderr
    assert "pais ingest" in proc.stderr
