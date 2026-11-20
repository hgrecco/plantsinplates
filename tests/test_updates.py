import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError
import zipfile

from plantsinplates import updates
from plantsinplates.version import __version__
from scripts.build_release import build_release, write_zip
from update_fixtures import ROOT, make_release


class UpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="plants update tests ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.old_assets = make_release(self.root / "old source", "2026.11.19")
        self.new_assets = make_release(self.root / "new source", "2026.11.20")
        self.install = self.root / "installed app"
        self.install.mkdir()
        for name, data in updates.validate_package(
            self.old_assets[1], "2026.11.19"
        ).items():
            (self.install / name).write_bytes(data)
        self.release = updates.Release(
            "2026.11.20",
            "v2026.11.20",
            updates.RELEASES_URL + "/tag/v2026.11.20",
            "https://example.com/package.zip",
            "https://example.com/SHA256SUMS",
        )

    def download(self, url, destination, **_kwargs) -> None:
        destination.write(
            (
                self.new_assets[2] if url.endswith("SHA256SUMS") else self.new_assets[1]
            ).read_bytes()
        )

    def stage(self) -> Path:
        with mock.patch.object(updates, "_download", side_effect=self.download):
            return updates.stage_update(
                self.install, self.release, report=lambda _: None
            )

    def run_logged(self, _root, command, *_args) -> str:
        return "2026.11.20\n" if command[-1] == "show-version" else ""

    def test_date_versions_compare_numerically_and_validate_dates(self) -> None:
        self.assertLess(
            updates.version_key("2026.11.20"), updates.version_key("2026.11.20.1")
        )
        self.assertLess(
            updates.version_key("2026.11.20.2"), updates.version_key("2026.11.20.10")
        )
        for value in (
            "0.2.0",
            "2026.02.30",
            "2026.11.20.0",
            "2026.11.20rc1",
            "2026.1.01",
        ):
            with self.subTest(value=value), self.assertRaises(updates.UpdateError):
                updates.version_key(value)

    def test_source_and_isolated_bundle_read_their_own_manifest(self) -> None:
        self.assertEqual(__version__, updates.installed_version(ROOT))
        for source, bundle, expected in (
            (self.root / "old source", self.old_assets[0], "2026.11.19"),
            (self.root / "new source", self.new_assets[0], "2026.11.20"),
        ):
            actual = subprocess.check_output(
                [sys.executable, "-I", str(bundle), "version"], cwd=self.root, text=True
            ).strip()
            self.assertEqual(actual, expected)
            self.assertEqual(updates.installed_version(source), expected)

    def test_build_is_reproducible_and_excludes_bytecode(self) -> None:
        source = self.root / "old source"
        cache = source / "plantsinplates" / "__pycache__"
        cache.mkdir()
        (cache / "unwanted.pyc").write_bytes(b"cache")
        again = build_release(source, self.root / "second build")
        for original, rebuilt in zip(self.old_assets, again):
            self.assertEqual(original.read_bytes(), rebuilt.read_bytes())
        with zipfile.ZipFile(again[0]) as bundle:
            self.assertFalse(any("__pycache__" in name for name in bundle.namelist()))

    def test_latest_release_requires_complete_official_assets(self) -> None:
        prefix = updates.RELEASES_URL + "/download/v2026.11.20/"
        payload = {
            "tag_name": "v2026.11.20",
            "draft": False,
            "prerelease": False,
            "assets": [
                {"name": name, "browser_download_url": prefix + name}
                for name in ("plantsinplates-2026.11.20.zip", "SHA256SUMS")
            ],
        }

        def download(_url, output, **_kwargs):
            output.write(json.dumps(payload).encode())

        with mock.patch.object(updates, "_download", side_effect=download):
            self.assertEqual(
                updates.check_for_update("2026.11.19").version, "2026.11.20"
            )
            self.assertIsNone(updates.check_for_update("2026.11.20"))
            self.assertIsNone(updates.check_for_update("2026.11.20.1"))
            payload["prerelease"] = True
            self.assertIsNone(updates.latest_release())
            payload["prerelease"] = False
            payload["assets"].pop()
            with self.assertRaisesRegex(updates.UpdateError, "missing update assets"):
                updates.latest_release()

    def test_no_published_release_is_a_normal_check_result(self) -> None:
        error = HTTPError(updates.LATEST_URL, 404, "Not Found", {}, None)
        with mock.patch.object(updates, "build_opener") as opener:
            opener.return_value.open.side_effect = error
            self.assertIsNone(updates.latest_release())
            opener.return_value.open.side_effect = URLError("offline")
            with self.assertRaisesRegex(updates.UpdateError, "Check your connection"):
                updates.latest_release()

    def test_check_does_not_write_installation_files(self) -> None:
        before = sorted(self.install.iterdir())
        with mock.patch.object(updates, "latest_release", return_value=self.release):
            self.assertEqual(updates.main(["--root", str(self.install), "--check"]), 0)
        self.assertEqual(sorted(self.install.iterdir()), before)

    def test_checksum_mismatch_and_cancel_leave_current_installation_intact(
        self,
    ) -> None:
        for cancelled in (False, True):
            event = threading.Event()

            def download(url, output, **kwargs):
                self.download(url, output, **kwargs)
                if not url.endswith("SHA256SUMS"):
                    if cancelled:
                        event.set()
                    else:
                        output.write(b"corrupt")

            with (
                mock.patch.object(updates, "_download", side_effect=download),
                self.assertRaises(updates.UpdateError),
            ):
                updates.stage_update(self.install, self.release, cancel=event)
            self.assertEqual(updates.installed_version(self.install), "2026.11.19")
            self.assertFalse(
                list((self.install / updates.UPDATE_DIRECTORY).glob("staging-*"))
            )

    def test_package_rejects_unexpected_paths_and_manifest_mismatch(self) -> None:
        files = updates.validate_package(self.new_assets[1], "2026.11.20")
        for change in ("path", "manifest", "damaged"):
            altered = dict(files)
            if change == "path":
                altered["../unsafe"] = b"unsafe"
            elif change == "manifest":
                altered["pixi.lock"] = b"different"
            else:
                altered["plantsinplates-app"] = b"not a ZIP"
            package = self.root / "invalid.zip"
            write_zip(package, altered)
            with self.subTest(change=change), self.assertRaises(updates.UpdateError):
                updates.validate_package(package, "2026.11.20")
        with self.assertRaisesRegex(updates.UpdateError, "version disagree"):
            updates.validate_package(self.new_assets[1], "2026.11.21")

    def test_success_replaces_only_application_files_and_keeps_backup(self) -> None:
        sentinel = (
            self.install / "experiment_001" / ".plantsinplates_cache" / "keep.bin"
        )
        sentinel.parent.mkdir(parents=True)
        sentinel.write_bytes(b"source, outputs and cache stay here")
        stage = self.stage()
        before = {
            name: (self.install / name).read_bytes() for name in updates.INSTALL_FILES
        }
        with (
            mock.patch.object(updates, "pixi_executable", return_value="pixi"),
            mock.patch.object(updates, "_run_logged", side_effect=self.run_logged),
        ):
            updates.apply_update(self.install, stage)
        self.assertEqual(updates.installed_version(self.install), "2026.11.20")
        self.assertEqual(sentinel.read_bytes(), b"source, outputs and cache stay here")
        for name, data in before.items():
            self.assertEqual((stage / "backup" / name).read_bytes(), data)
        self.assertFalse(
            (self.install / updates.UPDATE_DIRECTORY / "transaction.json").exists()
        )

    def test_changed_stage_and_development_checkouts_cannot_be_installed(self) -> None:
        stage = self.stage()
        (stage / "package.zip").write_bytes(b"changed")
        with self.assertRaisesRegex(updates.UpdateError, "staged download changed"):
            updates.apply_update(self.install, stage)
        (self.install / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
        with self.assertRaisesRegex(updates.UpdateError, "development checkout"):
            updates.stage_update(self.install, self.release)

    def test_running_apps_and_concurrent_updates_prevent_replacement(self) -> None:
        stage = self.stage()
        with (
            updates.application_session(self.install),
            updates.application_session(self.install),
        ):
            with self.assertRaisesRegex(updates.UpdateError, "Another application"):
                updates.apply_update(self.install, stage)
        with updates.FileLock(self.install / updates.UPDATE_DIRECTORY / "update.lock"):
            with self.assertRaises(updates.UpdateError):
                updates.apply_update(self.install, stage)
        self.assertEqual(updates.installed_version(self.install), "2026.11.19")

    def test_installation_failure_restores_files_and_old_environment(self) -> None:
        stage = self.stage()
        before = {
            name: (self.install / name).read_bytes() for name in updates.INSTALL_FILES
        }
        with (
            mock.patch.object(updates, "pixi_executable", return_value="pixi"),
            mock.patch.object(
                updates,
                "_run_logged",
                side_effect=[updates.UpdateError("dependency download failed"), ""],
            ) as command,
        ):
            with self.assertRaisesRegex(
                updates.UpdateError, "previous installation was restored"
            ):
                updates.apply_update(self.install, stage)
            self.assertEqual(command.call_count, 2)
        for name, data in before.items():
            self.assertEqual((self.install / name).read_bytes(), data)

    def test_failed_recovery_retains_journal_and_can_be_retried(self) -> None:
        stage = self.stage()
        with (
            mock.patch.object(updates, "pixi_executable", return_value="pixi"),
            mock.patch.object(
                updates, "_run_logged", side_effect=updates.UpdateError("offline")
            ),
        ):
            with self.assertRaisesRegex(updates.UpdateError, "recovery is incomplete"):
                updates.apply_update(self.install, stage)
        journal = self.install / updates.UPDATE_DIRECTORY / "transaction.json"
        self.assertTrue(journal.exists())
        with self.assertRaisesRegex(updates.UpdateError, "interrupted update"):
            with updates.application_session(self.install):
                self.fail("An incomplete update must block the application")
        with (
            mock.patch.object(updates, "pixi_executable", return_value="pixi"),
            mock.patch.object(updates, "_run_logged", return_value=""),
        ):
            self.assertEqual(
                updates.main(["--root", str(self.install), "--recover"]), 0
            )
        self.assertFalse(journal.exists())
        self.assertEqual(updates.installed_version(self.install), "2026.11.19")

    def test_standalone_updater_help_requires_no_scientific_imports(self) -> None:
        output = subprocess.check_output(
            [
                sys.executable,
                "-I",
                str(self.install / "plantsinplates-updater.py"),
                "--help",
            ],
            cwd=self.root,
            text=True,
        )
        self.assertIn("--check", output)
        self.assertIn("--recover", output)

    def test_cancelled_handoff_neither_installs_nor_reopens_the_app(self) -> None:
        stage = self.stage()
        (stage / "cancel").touch()
        with (
            mock.patch.object(updates, "_pid_running", return_value=False),
            mock.patch.object(updates, "apply_update") as install,
            mock.patch.object(updates.subprocess, "Popen") as reopen,
        ):
            self.assertEqual(
                updates.main(
                    [
                        "--root",
                        str(self.install),
                        "--stage",
                        str(stage),
                        "--wait-pid",
                        "123",
                        "--restart",
                        "",
                    ]
                ),
                0,
            )
            install.assert_not_called()
            reopen.assert_not_called()

    def test_subprocess_output_keeps_pixi_diagnostics_out_of_the_version(self) -> None:
        with mock.patch.dict(os.environ, {"PYTHONIOENCODING": "cp1252"}):
            output = updates._run_logged(
                self.install,
                [
                    sys.executable,
                    "-c",
                    "import sys; print('2026.11.20'); "
                    "print('Pixi diagnostics\\u2026 \\u03b1', file=sys.stderr)",
                ],
            )
        self.assertEqual(output.strip(), "2026.11.20")
        log = self.install / updates.UPDATE_DIRECTORY / "update.log"
        self.assertIn("Pixi diagnostics… α", log.read_text(encoding="utf-8"))

    def test_non_utf8_subprocess_output_preserves_command_result(self) -> None:
        for returncode in (0, 1):
            with self.subTest(returncode=returncode):
                command = [
                    sys.executable,
                    "-c",
                    "import sys; "
                    "sys.stdout.buffer.write(b'output\\x85\\n'); "
                    "sys.stderr.buffer.write(b'diagnostics\\x85\\n'); "
                    f"sys.exit({returncode})",
                ]
                if returncode:
                    with self.assertRaisesRegex(
                        updates.UpdateError, "Pixi could not complete the update"
                    ):
                        updates._run_logged(self.install, command)
                else:
                    self.assertEqual(
                        updates._run_logged(self.install, command), "output\ufffd\n"
                    )
                log = self.install / updates.UPDATE_DIRECTORY / "update.log"
                text = log.read_text(encoding="utf-8")
                self.assertIn("output\ufffd\n", text)
                self.assertIn("diagnostics\ufffd\n", text)

    def test_helper_environment_preserves_shell_paths_without_active_python(
        self,
    ) -> None:
        active = self.install / ".pixi" / "envs" / "default"
        system = self.root / "system bin"
        environment = {
            "PATH": os.pathsep.join((str(active / "bin"), str(system))),
            "CONDA_PREFIX": str(active),
            "PIXI_ENVIRONMENT_NAME": "default",
            "PYTHONPATH": "unrelated source",
            "PYTHONIOENCODING": "cp1252",
        }
        with (
            mock.patch.dict(os.environ, environment, clear=True),
            mock.patch.object(
                updates,
                "pixi_executable",
                return_value=str(self.root / "pixi bin" / "pixi"),
            ),
        ):
            result = updates._subprocess_environment(self.install)
        paths = result["PATH"].split(os.pathsep)
        self.assertIn(str(system), paths)
        self.assertIn(str(self.root / "pixi bin"), paths)
        self.assertNotIn(str(active / "bin"), paths)
        self.assertNotIn("CONDA_PREFIX", result)
        self.assertNotIn("PIXI_ENVIRONMENT_NAME", result)
        self.assertNotIn("PYTHONPATH", result)
        self.assertEqual(result["PYTHONIOENCODING"], "utf-8")


if __name__ == "__main__":
    unittest.main()
