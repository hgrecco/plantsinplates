"""Small executable releases for updater behavior and subprocess integration."""

from pathlib import Path
import platform

from scripts.build_release import build_release

ROOT = Path(__file__).resolve().parents[1]
APPLICATION = """import sys
from pathlib import Path
from plantsinplates import updates
from plantsinplates.version import __version__

if sys.argv[1:2] == ["handoff"]:
    with updates.application_session(updates.application_root()):
        updates.launch_installer(updates.application_root(), Path(sys.argv[2]), restart_folder=sys.argv[3])
elif sys.argv[1:2] == ["gui"] and "--help" not in sys.argv:
    import six
    Path(sys.argv[2]).write_text(__version__, encoding="utf-8")
else:
    print("Fixture application help")
"""


def make_release(
    root: Path, version: str, *, real_lock: bool = False, extra_dependency: bool = False
) -> tuple[Path, Path, Path]:
    root.mkdir(parents=True)
    package = root / "plantsinplates"
    package.mkdir()
    for name in ("updates.py", "version.py"):
        (package / name).write_bytes((ROOT / "plantsinplates" / name).read_bytes())
    (root / "__main__.py").write_text(APPLICATION, encoding="utf-8")
    for name in ("plantsinplates-updater.py", "LICENSE"):
        (root / name).write_bytes((ROOT / name).read_bytes())
    host = (
        "win-64"
        if platform.system() == "Windows"
        else "osx-arm64"
        if platform.system() == "Darwin"
        else "linux-64"
    )
    dependency = '\nsix = "==1.17.0"' if extra_dependency else ""
    manifest = f'''[workspace]
name = "update-fixture"
version = "{version}"
channels = ["https://prefix.dev/conda-forge"]
platforms = ["{host}"]

[dependencies]
python = "==3.12"{dependency}

[tasks]
show-version = "python plantsinplates-app version"
gui = "python plantsinplates-app gui"

[feature.updater.dependencies]
python = "==3.12"

[feature.updater.tasks]
update-app = "python plantsinplates-updater.py"

[environments]
updater = {{ features = ["updater"], no-default-feature = true }}
'''
    (root / "pixi.toml").write_text(manifest, encoding="utf-8")
    if real_lock:
        import subprocess

        subprocess.run(
            ["pixi", "lock", "--manifest-path", str(root / "pixi.toml")],
            check=True,
            capture_output=True,
            text=True,
        )
    else:
        (root / "pixi.lock").write_text("fixture lock", encoding="utf-8")
    return build_release(root, root / "dist")
