"""`pais-dev` is a removal redirect in v0.4. This file just verifies the shim
exits 1 with a useful message."""

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
