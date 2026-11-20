"""Opt-in real Pixi install, dependency change, process handoff, and restart."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from plantsinplates import updates
from update_fixtures import make_release


@unittest.skipUnless(
    os.environ.get("PLANTSINPLATES_UPDATE_INTEGRATION") == "1",
    "Set PLANTSINPLATES_UPDATE_INTEGRATION=1 to run real Pixi installation",
)
class PixiUpdateIntegrationTests(unittest.TestCase):
    def test_dependency_change_handoff_and_restart_in_path_with_spaces(self) -> None:
        with tempfile.TemporaryDirectory(prefix="plants real update ") as temporary:
            root = Path(temporary)
            old = make_release(root / "old source", "2026.11.19", real_lock=True)
            new = make_release(
                root / "new source", "2026.11.20", real_lock=True, extra_dependency=True
            )
            installation = root / "installed application"
            installation.mkdir()
            for name, data in updates.validate_package(old[1], "2026.11.19").items():
                (installation / name).write_bytes(data)
            manifest = str(installation / "pixi.toml")
            subprocess.run(
                ["pixi", "install", "--locked", "--manifest-path", manifest],
                check=True,
                capture_output=True,
                text=True,
            )
            updates.prepare_updater(installation)
            old_has_six = subprocess.check_output(
                [
                    "pixi",
                    "run",
                    "--locked",
                    "--manifest-path",
                    manifest,
                    "python",
                    "-c",
                    "import importlib.util; print(importlib.util.find_spec('six') is not None)",
                ],
                text=True,
            ).strip()
            self.assertEqual(old_has_six, "False")
            release = updates.Release(
                "2026.11.20",
                "v2026.11.20",
                "https://example.com/notes",
                "https://example.com/package.zip",
                "https://example.com/SHA256SUMS",
            )

            def download(url, output, **_kwargs):
                output.write(
                    (new[2] if url.endswith("SHA256SUMS") else new[1]).read_bytes()
                )

            with mock.patch.object(updates, "_download", side_effect=download):
                stage = updates.stage_update(installation, release)
            marker = root / "restarted.txt"
            subprocess.run(
                [
                    "pixi",
                    "run",
                    "--locked",
                    "--manifest-path",
                    manifest,
                    "python",
                    "plantsinplates-app",
                    "handoff",
                    str(stage),
                    str(marker),
                ],
                cwd=installation,
                env={**os.environ, "PYTHONIOENCODING": "cp1252"},
                check=True,
                capture_output=True,
                text=True,
                timeout=90,
            )
            deadline = time.monotonic() + 180
            result_path = installation / updates.UPDATE_DIRECTORY / "result.json"
            while not marker.exists() and time.monotonic() < deadline:
                if result_path.exists():
                    result = json.loads(result_path.read_text(encoding="utf-8"))
                    logs = installation / updates.UPDATE_DIRECTORY
                    details = "\n".join(
                        path.read_text(encoding="utf-8", errors="replace")
                        for path in logs.glob("*.log")
                    )
                    self.assertTrue(
                        result["success"], result["message"] + "\n" + details
                    )
                time.sleep(0.2)
            log = installation / updates.UPDATE_DIRECTORY / "helper.log"
            self.assertTrue(
                marker.exists(),
                log.read_text(encoding="utf-8", errors="replace")
                if log.exists()
                else "No helper log",
            )
            self.assertEqual(marker.read_text(encoding="utf-8"), "2026.11.20")
            self.assertEqual(updates.installed_version(installation), "2026.11.20")
            self.assertTrue((stage / "backup" / "pixi.lock").is_file())


if __name__ == "__main__":
    unittest.main()
