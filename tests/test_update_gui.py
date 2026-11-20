from pathlib import Path
import tempfile
import threading
import unittest

from plantsinplates.update_dialog import UpdateDialog
from test_gui_workflow import gui


class UpdateDecisionTests(unittest.TestCase):
    def test_installation_requires_all_existing_workers_to_be_idle(self) -> None:
        app = object.__new__(gui.AnalyzeApp)
        fields = (
            "is_busy",
            "templates_in_progress",
            "folder_check_in_progress",
            "audit_in_progress",
            "closing",
        )
        for field in fields:
            setattr(app, field, False)
        self.assertTrue(app.can_install_update())
        for field in fields:
            with self.subTest(field=field):
                setattr(app, field, True)
                self.assertFalse(app.can_install_update())
                setattr(app, field, False)

    def test_cancelling_a_staged_handoff_signals_the_detached_helper(self) -> None:
        dialog = object.__new__(UpdateDialog)
        dialog.cancel_event = threading.Event()
        dialog.restarting = False
        with tempfile.TemporaryDirectory() as temporary:
            dialog.stage = Path(temporary)
            dialog.cancel_before_close()
            self.assertTrue(dialog.cancel_event.is_set())
            self.assertTrue((dialog.stage / "cancel").exists())

    def test_requested_restart_does_not_cancel_its_own_installation(self) -> None:
        dialog = object.__new__(UpdateDialog)
        dialog.cancel_event = threading.Event()
        dialog.restarting = True
        dialog.stage = None
        dialog.cancel_before_close()
        self.assertFalse(dialog.cancel_event.is_set())


if __name__ == "__main__":
    unittest.main()
