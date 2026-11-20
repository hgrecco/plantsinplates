"""Validate a release tag before creating any GitHub release."""

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from plantsinplates.updates import (  # noqa: E402
    REPOSITORY,
    UpdateError,
    installed_version,
    latest_release,
    version_key,
)


def check_tag(tag: str) -> None:
    version = installed_version(ROOT)
    version_key(version)
    if tag != f"v{version}":
        raise UpdateError(f"Tag {tag!r} must match the manifest version v{version}.")
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", "HEAD", "origin/main"],
        cwd=ROOT,
        check=True,
    )
    release = latest_release()
    if release and version_key(release.version) >= version_key(version):
        raise UpdateError(f"The release version must be newer than {release.version}.")
    print(f"Validated {REPOSITORY} release {tag}.")


if __name__ == "__main__":
    try:
        tag = os.environ.get("RELEASE_TAG", "")
        if tag:
            check_tag(tag)
        else:
            version_key(installed_version(ROOT))
    except (UpdateError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
