"""Run domain imports with site-packages disabled to prove isolation."""

import subprocess
import sys
from pathlib import Path


def test_core_imports_without_site_packages() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            "import sys, importlib, pkgutil; "
            f"sys.path.insert(0, {str(root / 'packages')!r}); "
            "import nexora; "
            "modules = list(pkgutil.walk_packages(nexora.__path__, nexora.__name__ + '.')); "
            "assert len(modules) >= 8; "
            "[importlib.import_module(m.name) for m in modules]",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
