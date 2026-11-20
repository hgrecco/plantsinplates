"""Check the installation ZIP away from the checkout, without a display."""

from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from plantsinplates.updates import installed_version, validate_package  # noqa: E402


def smoke_release(package: Path, version: str) -> None:
    files = validate_package(package, version)
    with tempfile.TemporaryDirectory(prefix="plants in plates smoke ") as temporary:
        root = Path(temporary)
        for name, data in files.items():
            (root / name).write_bytes(data)

        def run(*arguments: str) -> str:
            return subprocess.check_output(
                [sys.executable, "-I", *arguments], cwd=root, text=True
            ).strip()

        assert run(str(root / "plantsinplates-app"), "version") == version
        assert "Check or install" in run(
            str(root / "plantsinplates-updater.py"), "--help"
        )
        run(str(root / "plantsinplates-app"), "gui", "--help")
        run(str(root / "plantsinplates-app"), "test", "--help")
        with zipfile.ZipFile(root / "plantsinplates-app") as archive:
            assert not any(
                "__pycache__" in name or name.endswith(".pyc")
                for name in archive.namelist()
            )
        print(f"Installation ZIP smoke checks passed for {version}.")


if __name__ == "__main__":
    version = installed_version(ROOT)
    smoke_release(ROOT / "dist" / f"plantsinplates-{version}.zip", version)
