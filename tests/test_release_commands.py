import unittest
from unittest import mock

from plantsinplates import updates
from scripts import check_release, publish_release


class ReleaseCommandTests(unittest.TestCase):
    def test_tag_must_match_canonical_version(self) -> None:
        with (
            mock.patch.object(
                check_release, "installed_version", return_value="2026.11.20"
            ),
            mock.patch.object(check_release.subprocess, "run") as command,
        ):
            with self.assertRaisesRegex(updates.UpdateError, "must match"):
                check_release.check_tag("v2026.11.19")
            command.assert_not_called()

    def test_tag_requires_main_ancestry_and_a_newer_version(self) -> None:
        latest = updates.Release("2026.11.20", "v2026.11.20", "", "", "")
        with (
            mock.patch.object(
                check_release, "installed_version", return_value="2026.11.20"
            ),
            mock.patch.object(check_release.subprocess, "run") as command,
            mock.patch.object(check_release, "latest_release", return_value=latest),
        ):
            with self.assertRaisesRegex(updates.UpdateError, "must be newer"):
                check_release.check_tag("v2026.11.20")
            self.assertEqual(
                command.call_args.args[0],
                ["git", "merge-base", "--is-ancestor", "HEAD", "origin/main"],
            )

    def test_local_publish_refuses_uncommitted_changes_before_network_or_tagging(
        self,
    ) -> None:
        with (
            mock.patch.object(publish_release, "git", return_value=" M file.py"),
            mock.patch.object(publish_release, "latest_release") as release,
            mock.patch.object(publish_release.subprocess, "run") as command,
        ):
            with self.assertRaisesRegex(updates.UpdateError, "working-tree changes"):
                publish_release.publish()
            release.assert_not_called()
            command.assert_not_called()

    def test_local_publish_pushes_only_the_matching_tag_after_validation(self) -> None:
        def git(*arguments):
            return {
                ("status", "--porcelain"): "",
                ("branch", "--show-current"): "main",
                (
                    "remote",
                    "get-url",
                    "origin",
                ): f"git@github.com:{updates.REPOSITORY}.git",
                ("rev-parse", "HEAD"): "commit",
                ("rev-parse", "origin/main"): "commit",
                ("tag", "--list", "v2026.11.20"): "",
            }[arguments]

        with (
            mock.patch.object(publish_release, "git", side_effect=git),
            mock.patch.object(
                publish_release, "installed_version", return_value="2026.11.20"
            ),
            mock.patch.object(publish_release, "latest_release", return_value=None),
            mock.patch.object(publish_release.subprocess, "run") as command,
        ):
            self.assertEqual(publish_release.publish(), "v2026.11.20")
            self.assertEqual(
                [call.args[0] for call in command.call_args_list],
                [
                    ["git", "fetch", "origin", "main", "--tags"],
                    [
                        "git",
                        "tag",
                        "-a",
                        "v2026.11.20",
                        "-m",
                        "Plants in Plates 2026.11.20",
                    ],
                    ["git", "push", "origin", "refs/tags/v2026.11.20"],
                ],
            )


if __name__ == "__main__":
    unittest.main()
