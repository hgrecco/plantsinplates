"""GitHub releases and recoverable installation, using only the standard library.

This module is also copied to a staging directory and run by the independent
Pixi updater environment after the application has exited.
"""

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from typing import Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid
import zipfile

REPOSITORY = "hgrecco/plantsinplates"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
LATEST_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
UPDATE_DIRECTORY = ".plantsinplates_updates"
INSTALL_FILES = (
    "plantsinplates-app",
    "pixi.toml",
    "pixi.lock",
    "plantsinplates-updater.py",
    "INSTALL.md",
    "LICENSE",
)
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
MAX_UNPACKED_BYTES = 128 * 1024 * 1024
_PIXI_EXECUTABLE: str | None = None
type Reporter = Callable[[str], None]


class UpdateError(Exception):
    """An update could not be completed safely."""


class UpdateCancelled(UpdateError):
    """The user cancelled before installation started."""


@dataclass(frozen=True)
class Release:
    version: str
    tag: str
    notes_url: str
    archive_url: str
    checksums_url: str

    @property
    def archive_name(self) -> str:
        return f"plantsinplates-{self.version}.zip"


def version_key(value: str) -> tuple[int, int, int, int]:
    """Validate a calendar version, treating an omitted revision as zero."""
    match = re.fullmatch(r"(\d{4})\.(\d{2})\.(\d{2})(?:\.([1-9]\d*))?", value)
    if match is None:
        raise UpdateError(f"Invalid date version: {value!r}.")
    year, month, day = (int(part) for part in match.groups()[:3])
    try:
        date(year, month, day)
    except ValueError as error:
        raise UpdateError(f"Invalid date version: {value!r}.") from error
    return year, month, day, int(match[4] or 0)


def installed_version(root: Path) -> str:
    return tomllib.loads((root / "pixi.toml").read_text(encoding="utf-8"))["workspace"][
        "version"
    ]


def application_root() -> Path:
    """Locate the source checkout or enclosing application archive."""
    container = Path(__file__).resolve().parent.parent
    return container.parent if container.is_file() else container


def is_development_checkout(root: Path) -> bool:
    return (root / ".git").exists() or (root / "__main__.py").is_file()


def validate_installation(root: Path) -> None:
    if is_development_checkout(root):
        raise UpdateError(
            "This is a development checkout. Update it with Git, or install the "
            "GitHub release ZIP in a separate folder."
        )
    for name in INSTALL_FILES[:4]:
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise UpdateError(
                f"Incomplete installation: {path}. Download the release ZIP again."
            )


def _cancelled(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise UpdateCancelled(
            "Update cancelled; the installed application is unchanged."
        )


class _HTTPSRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        if urlparse(new_url).scheme != "https":
            raise UpdateError("GitHub redirected the download to a non-HTTPS address.")
        return super().redirect_request(request, fp, code, message, headers, new_url)


def _download(
    url: str,
    destination,
    *,
    limit: int,
    cancel: threading.Event | None = None,
    report: Reporter = print,
) -> None:
    if urlparse(url).scheme != "https":
        raise UpdateError("Updates require an HTTPS download address.")
    request = Request(
        url,
        headers={
            "User-Agent": "Plants-in-Plates-updater",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with build_opener(_HTTPSRedirects()).open(request, timeout=30) as response:
            total = 0
            last_report = 0
            while True:
                _cancelled(cancel)
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise UpdateError("The GitHub download exceeds the allowed size.")
                destination.write(chunk)
                if total - last_report >= 1024 * 1024:
                    report(f"Downloaded {total / (1024 * 1024):.1f} MB…")
                    last_report = total
    except (URLError, TimeoutError, OSError) as error:
        raise UpdateError(
            f"Could not download from GitHub: {error}. Check your connection and retry."
        ) from error


def latest_release() -> Release | None:
    """Return the latest complete stable release; public access needs no token."""
    content = io.BytesIO()
    try:
        _download(LATEST_URL, content, limit=2 * 1024 * 1024)
    except UpdateError as error:
        if isinstance(error.__cause__, HTTPError) and error.__cause__.code == 404:
            return None
        raise
    try:
        payload = json.loads(content.getvalue())
        if payload["draft"] or payload["prerelease"]:
            return None
        tag = payload["tag_name"]
        if not isinstance(tag, str) or not tag.startswith("v"):
            raise ValueError("release tag must begin with v")
        version = tag[1:]
        version_key(version)
        prefix = f"{RELEASES_URL}/download/{tag}/"
        assets = {
            asset["name"]: asset["browser_download_url"] for asset in payload["assets"]
        }
        archive_name = f"plantsinplates-{version}.zip"
        archive_url = assets[archive_name]
        checksums_url = assets["SHA256SUMS"]
        if (
            archive_url != prefix + archive_name
            or checksums_url != prefix + "SHA256SUMS"
        ):
            raise ValueError("unexpected release download address")
        return Release(
            version, tag, f"{RELEASES_URL}/tag/{tag}", archive_url, checksums_url
        )
    except (KeyError, TypeError, ValueError) as error:
        raise UpdateError(
            "The latest GitHub release has invalid metadata or missing update assets."
        ) from error


def check_for_update(current_version: str) -> Release | None:
    current = version_key(current_version)
    release = latest_release()
    return release if release and version_key(release.version) > current else None


def validate_package(package: Path, expected_version: str) -> dict[str, bytes]:
    """Validate the complete allowlisted package without extracting ZIP paths."""
    try:
        with zipfile.ZipFile(package) as archive:
            entries = archive.infolist()
            if len(entries) != len(INSTALL_FILES) or {
                entry.filename for entry in entries
            } != set(INSTALL_FILES):
                raise UpdateError(
                    "The update ZIP does not contain the expected installation files."
                )
            if sum(entry.file_size for entry in entries) > MAX_UNPACKED_BYTES:
                raise UpdateError("The unpacked update exceeds the allowed size.")
            files = {name: archive.read(name) for name in INSTALL_FILES}
        manifest = tomllib.loads(files["pixi.toml"].decode("utf-8"))
        if manifest["workspace"]["version"] != expected_version:
            raise UpdateError("The release tag and packaged version disagree.")
        version_key(expected_version)
        with zipfile.ZipFile(io.BytesIO(files["plantsinplates-app"])) as bundle:
            if sum(entry.file_size for entry in bundle.infolist()) > MAX_UNPACKED_BYTES:
                raise UpdateError("The application bundle exceeds the allowed size.")
            for name in (
                "__main__.py",
                "plantsinplates/updates.py",
                "plantsinplates/version.py",
            ):
                bundle.getinfo(name)
            for name in ("pixi.toml", "pixi.lock"):
                if bundle.read(name) != files[name]:
                    raise UpdateError(f"The bundled and external {name} disagree.")
            if bundle.testzip() is not None:
                raise UpdateError("The application bundle is damaged.")
        return files
    except (
        zipfile.BadZipFile,
        KeyError,
        UnicodeError,
        ValueError,
        RuntimeError,
    ) as error:
        raise UpdateError(f"Invalid update package: {error}.") from error


def _module_bytes() -> bytes:
    loader = globals().get("__loader__")
    read_data = getattr(loader, "get_data", None)
    return read_data(__file__) if read_data else Path(__file__).read_bytes()


def _write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".new")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)


def stage_update(
    root: Path,
    release: Release,
    *,
    cancel: threading.Event | None = None,
    report: Reporter = print,
) -> Path:
    validate_installation(root)
    if version_key(release.version) <= version_key(installed_version(root)):
        raise UpdateError("This release is not newer than the installed application.")
    work = root / UPDATE_DIRECTORY
    work.mkdir(exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="staging-", dir=work))
    try:
        report(f"Downloading Plants in Plates {release.version}…")
        checksums = io.BytesIO()
        _download(release.checksums_url, checksums, limit=64 * 1024, cancel=cancel)
        expected = None
        for line in checksums.getvalue().decode("utf-8").splitlines():
            fields = line.split()
            if (
                len(fields) == 2
                and fields[1] == release.archive_name
                and re.fullmatch(r"[0-9a-f]{64}", fields[0])
            ):
                if expected is not None:
                    raise UpdateError("Duplicate checksum entry in the release.")
                expected = fields[0]
        if expected is None:
            raise UpdateError(
                "The release does not provide a SHA-256 checksum for its installation ZIP."
            )
        package = stage / "package.zip"
        with package.open("wb") as output:
            _download(
                release.archive_url,
                output,
                limit=MAX_DOWNLOAD_BYTES,
                cancel=cancel,
                report=report,
            )
        _cancelled(cancel)
        report("Verifying the download…")
        if hashlib.sha256(package.read_bytes()).hexdigest() != expected:
            raise UpdateError("The download checksum does not match. Retry the update.")
        validate_package(package, release.version)
        (stage / "helper.py").write_bytes(_module_bytes())
        _write_json(
            stage / "release.json", {"version": release.version, "sha256": expected}
        )
        return stage
    except Exception:
        shutil.rmtree(stage)
        raise


class FileLock:
    """Hold an OS lock, which is automatically released when a process exits."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                if self.file.seek(0, os.SEEK_END) == 0:
                    self.file.write(b"0")
                    self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.file.close()
            raise UpdateError(
                "Another application instance or update is using this installation. Close it and retry."
            ) from error

    def close(self) -> None:
        self.file.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


@contextmanager
def application_session(root: Path) -> Iterator[None]:
    """Register running bundled GUI/batch processes without blocking each other."""
    if is_development_checkout(root):
        yield
        return
    work = root / UPDATE_DIRECTORY
    session_path = work / "sessions" / f"{os.getpid()}-{uuid.uuid4().hex}.lock"
    with FileLock(work / "update.lock"):
        if (work / "transaction.json").exists():
            raise UpdateError(
                "An interrupted update needs recovery. See INSTALL.md and the update log."
            )
        session = FileLock(session_path)
    try:
        yield
    finally:
        session.close()
        session_path.unlink(missing_ok=True)


def _assert_no_sessions(root: Path) -> None:
    for path in (root / UPDATE_DIRECTORY / "sessions").glob("*.lock"):
        with FileLock(path):
            pass
        path.unlink(missing_ok=True)


def pixi_executable() -> str:
    path = _PIXI_EXECUTABLE or shutil.which("pixi")
    if not path:
        raise UpdateError(
            "Pixi was not found on PATH. Install Pixi, then retry the update."
        )
    return path


def _pixi(root: Path, *arguments: str) -> list[str]:
    return [pixi_executable(), *arguments, "--manifest-path", str(root / "pixi.toml")]


def _subprocess_environment(root: Path) -> dict[str, str]:
    """Drop the active Python environment while retaining OS shell utilities."""
    environment = os.environ.copy()
    prefixes = [root / ".pixi" / "envs", Path(sys.prefix)]
    if environment.get("CONDA_PREFIX"):
        prefixes.append(Path(environment["CONDA_PREFIX"]))
    for key in list(environment):
        if key.startswith(("PIXI_", "CONDA_")) or key in {
            "PYTHONPATH",
            "PYTHONHOME",
            "VIRTUAL_ENV",
            "TCL_LIBRARY",
            "TK_LIBRARY",
        }:
            environment.pop(key)
    paths = [str(Path(pixi_executable()).parent)]
    for entry in os.environ.get("PATH", os.defpath).split(os.pathsep):
        if entry and not any(
            Path(entry).resolve().is_relative_to(prefix.resolve())
            for prefix in prefixes
        ):
            paths.append(entry)
    paths.extend(os.defpath.split(os.pathsep))
    environment["PATH"] = os.pathsep.join(dict.fromkeys(paths))
    return environment


def _run_logged(
    root: Path, command: list[str], cancel: threading.Event | None = None
) -> str:
    work = root / UPDATE_DIRECTORY
    work.mkdir(exist_ok=True)
    with (work / "update.log").open("a", encoding="utf-8") as log:
        log.write(f"\n{time.strftime('%Y-%m-%d %H:%M:%S')} {command!r}\n")
        log.flush()
        with (
            tempfile.TemporaryFile(mode="w+", encoding="utf-8") as output,
            tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errors,
        ):
            process = subprocess.Popen(
                command,
                cwd=root,
                stdout=output,
                stderr=errors,
                env=_subprocess_environment(root),
            )
            try:
                while process.poll() is None:
                    _cancelled(cancel)
                    time.sleep(0.1)
            except UpdateCancelled:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise
            finally:
                output.seek(0)
                text = output.read()
                log.write(text)
                errors.seek(0)
                log.write(errors.read())
            if process.returncode:
                raise UpdateError(
                    f"Pixi could not complete the update. See {work / 'update.log'}."
                )
            return text


def prepare_updater(root: Path, *, cancel: threading.Event | None = None) -> None:
    validate_installation(root)
    _run_logged(
        root, _pixi(root, "install", "--locked", "--environment", "updater"), cancel
    )


def _replace_file(target: Path, data: bytes) -> None:
    temporary = target.with_name(target.name + ".update-new")
    temporary.write_bytes(data)
    temporary.replace(target)


def recover_installation(root: Path) -> None:
    """Restore the recorded backup and its locked environment after a failure."""
    journal = root / UPDATE_DIRECTORY / "transaction.json"
    transaction = json.loads(journal.read_text(encoding="utf-8"))
    backup = Path(transaction["backup"])
    for name in INSTALL_FILES:
        if name in transaction["previous_files"]:
            _replace_file(root / name, (backup / name).read_bytes())
        else:
            (root / name).unlink(missing_ok=True)
    _run_logged(root, _pixi(root, "install", "--locked", "--environment", "default"))
    journal.unlink()


def apply_update(root: Path, stage: Path, *, report: Reporter = print) -> None:
    """Install a verified staged release while no application process is running."""
    validate_installation(root)
    pixi_executable()
    work = root / UPDATE_DIRECTORY
    with FileLock(work / "update.lock"):
        _assert_no_sessions(root)
        journal = work / "transaction.json"
        if journal.exists():
            raise UpdateError(
                "An earlier update needs recovery before another update can start."
            )
        metadata = json.loads((stage / "release.json").read_text(encoding="utf-8"))
        package = stage / "package.zip"
        if hashlib.sha256(package.read_bytes()).hexdigest() != metadata["sha256"]:
            raise UpdateError("The staged download changed. Download the update again.")
        files = validate_package(package, metadata["version"])
        if version_key(metadata["version"]) <= version_key(installed_version(root)):
            raise UpdateError(
                "The staged release is not newer than the installed application."
            )
        backup = stage / "backup"
        backup.mkdir()
        previous = []
        for name in INSTALL_FILES:
            path = root / name
            if path.is_symlink():
                raise UpdateError(f"Cannot replace a symbolic link: {path}.")
            if path.exists():
                shutil.copy2(path, backup / name)
                previous.append(name)
        _write_json(journal, {"backup": str(backup), "previous_files": previous})
        try:
            report("Installing the update and its locked environment…")
            for name, data in files.items():
                _replace_file(root / name, data)
            _run_logged(
                root, _pixi(root, "install", "--locked", "--environment", "default")
            )
            command = [
                pixi_executable(),
                "run",
                "--locked",
                "--environment",
                "default",
                "--manifest-path",
                str(root / "pixi.toml"),
            ]
            actual = _run_logged(root, [*command, "show-version"]).strip()
            if actual != metadata["version"]:
                raise UpdateError(
                    "The installed application did not report the expected version."
                )
            _run_logged(
                root, [*command, "python", "plantsinplates-app", "gui", "--help"]
            )
        except Exception as error:
            try:
                recover_installation(root)
            except Exception as recovery_error:
                raise UpdateError(
                    f"Update failed and recovery is incomplete. Backup: {backup}. See INSTALL.md and {work / 'update.log'}. {recovery_error}"
                ) from error
            raise UpdateError(
                f"Update failed; the previous installation was restored. {error}"
            ) from error
        journal.unlink()
        report(f"Updated to {metadata['version']}.")


def _pid_running(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def launch_installer(
    root: Path,
    stage: Path,
    *,
    restart_folder: str | None = None,
    cancel: threading.Event | None = None,
) -> None:
    """Wait for the independent helper's readiness before the GUI may exit."""
    _cancelled(cancel)
    ready = stage / "ready"
    command = [
        pixi_executable(),
        "run",
        "--locked",
        "--environment",
        "updater",
        "--manifest-path",
        str(root / "pixi.toml"),
        "python",
        str(stage / "helper.py"),
        "--root",
        str(root),
        "--stage",
        str(stage),
        "--wait-pid",
        str(os.getpid()),
        "--ready-file",
        str(ready),
        "--pixi-executable",
        pixi_executable(),
    ]
    if restart_folder is not None:
        command.extend(["--restart", restart_folder])
    with (root / UPDATE_DIRECTORY / "helper.log").open("a", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            cwd=root,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=_subprocess_environment(root),
        )
    deadline = time.monotonic() + 30
    while not ready.exists():
        try:
            _cancelled(cancel)
            if process.poll() is not None or time.monotonic() >= deadline:
                raise UpdateError(
                    "The update helper did not start. See the helper log; the application remains open."
                )
        except UpdateError:
            (stage / "cancel").touch()
            process.terminate()
            raise
        time.sleep(0.1)
    if cancel is not None and cancel.is_set():
        (stage / "cancel").touch()
    _cancelled(cancel)


def main(argv: list[str] | None = None) -> int:
    global _PIXI_EXECUTABLE
    parser = argparse.ArgumentParser(
        description="Check or install a stable GitHub release."
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "--check", action="store_true", help="Only check for a newer release."
    )
    parser.add_argument(
        "--recover", action="store_true", help="Restore an interrupted update."
    )
    parser.add_argument("--stage", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--wait-pid", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--ready-file", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--restart", help=argparse.SUPPRESS)
    parser.add_argument("--pixi-executable", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    _PIXI_EXECUTABLE = args.pixi_executable
    root = args.root.resolve()
    result = 0
    message = ""
    try:
        if args.recover:
            with FileLock(root / UPDATE_DIRECTORY / "update.lock"):
                _assert_no_sessions(root)
                recover_installation(root)
            print("Previous installation restored.")
            return 0
        if args.check:
            release = check_for_update(installed_version(root))
            print(
                f"Update available: {release.version}\n{release.notes_url}"
                if release
                else "No newer stable release is available."
            )
            return 0
        validate_installation(root)
        stage = args.stage
        if stage is None:
            release = check_for_update(installed_version(root))
            if release is None:
                print("No newer stable release is available.")
                return 0
            stage = stage_update(root, release)
        if args.ready_file:
            args.ready_file.touch()
        if args.wait_pid:
            deadline = time.monotonic() + 120
            while _pid_running(args.wait_pid):
                if (stage / "cancel").exists():
                    raise UpdateCancelled("Update cancelled before installation.")
                if time.monotonic() >= deadline:
                    raise UpdateError(
                        "The application did not exit; the update was not installed."
                    )
                time.sleep(0.2)
        if (stage / "cancel").exists():
            raise UpdateCancelled("Update cancelled before installation.")
        apply_update(root, stage)
        message = f"Plants in Plates was updated to {installed_version(root)}."
    except UpdateCancelled as error:
        print(str(error))
        return 0
    except (UpdateError, OSError, ValueError) as error:
        message = str(error)
        print(message, file=sys.stderr)
        result = 1
    if args.restart is not None:
        work = root / UPDATE_DIRECTORY
        _write_json(work / "result.json", {"success": result == 0, "message": message})
        if not (work / "transaction.json").exists() and (
            not args.wait_pid or not _pid_running(args.wait_pid)
        ):
            subprocess.Popen(
                [
                    pixi_executable(),
                    "run",
                    "--locked",
                    "--environment",
                    "default",
                    "--manifest-path",
                    str(root / "pixi.toml"),
                    "gui",
                    args.restart,
                ],
                cwd=root,
                start_new_session=True,
                env=_subprocess_environment(root),
            )
    return result


if __name__ == "__main__":
    raise SystemExit(main())
