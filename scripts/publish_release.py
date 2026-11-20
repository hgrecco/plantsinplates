"""Push a version tag; GitHub Actions builds and publishes the release."""

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


def git(*arguments: str) -> str:
    return subprocess.check_output(["git", *arguments], cwd=ROOT, text=True).strip()


def publish() -> str:
    version = installed_version(ROOT)
    version_key(version)
    if git("status", "--porcelain"):
        raise UpdateError("Commit or remove working-tree changes before publishing.")
    if git("branch", "--show-current") != "main":
        raise UpdateError("Publish from the main branch.")
    remote = git("remote", "get-url", "origin")
    if remote not in (
        f"git@github.com:{REPOSITORY}.git",
        f"https://github.com/{REPOSITORY}.git",
        f"https://github.com/{REPOSITORY}",
    ):
        raise UpdateError(
            "The origin remote is not the official Plants in Plates repository."
        )
    latest = latest_release()
    if latest and version_key(version) <= version_key(latest.version):
        raise UpdateError(
            f"Choose a version newer than {latest.version} before publishing."
        )
    subprocess.run(["git", "fetch", "origin", "main", "--tags"], cwd=ROOT, check=True)
    if git("rev-parse", "HEAD") != git("rev-parse", "origin/main"):
        raise UpdateError(
            "Push main and synchronize it with origin/main before publishing."
        )
    tag = f"v{version}"
    if git("tag", "--list", tag):
        raise UpdateError(
            f"Tag {tag} already exists. Use a new version, or push the existing tag manually."
        )
    subprocess.run(
        ["git", "tag", "-a", tag, "-m", f"Plants in Plates {version}"],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(["git", "push", "origin", f"refs/tags/{tag}"], cwd=ROOT, check=True)
    return tag


if __name__ == "__main__":
    try:
        print(f"Pushed {publish()}. Follow the release workflow on GitHub.")
    except (UpdateError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
