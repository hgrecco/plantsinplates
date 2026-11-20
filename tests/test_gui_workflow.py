"""Workflow-level checks for the desktop UI's non-visual decision logic."""

import importlib.util
import pathlib
import sys
import tempfile
import threading
import unittest
from unittest import mock

from openpyxl import Workbook
import ttkbootstrap as ttk


ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location(
    "plantsinplates_gui", ROOT / "__main__.py"
)
assert SPEC and SPEC.loader
gui = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gui)


def write_info(path: pathlib.Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["row", "col", "genotype", "20260101_length", "20260101_fluo"])
    sheet.append([1, 1, "control", 10, "row_001/fluo_1_1.czi"])
    workbook.save(path)


def write_referenced_image(plate: pathlib.Path, date: str = "20260101") -> None:
    image = plate / f"date_{date}" / "row_001" / "fluo_1_1.czi"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"test image placeholder")


class WorkflowValidationTests(unittest.TestCase):
    def test_gui_uses_themed_window_and_spacing_scale(self) -> None:
        self.assertTrue(issubclass(gui.AnalyzeApp, ttk.Window))
        self.assertEqual(
            (gui.SPACE_XS, gui.SPACE_SM, gui.SPACE_MD, gui.SPACE_LG),
            (4, 8, 16, 24),
        )

    def test_method_dependent_fields_are_clear_and_complete(self) -> None:
        app = object.__new__(gui.AnalyzeApp)
        app.method_var = type("Variable", (), {"get": lambda self: "box"})()
        self.assertEqual(
            [item[0] for item in app._settings_for_method()], ["box_size", "box_offset"]
        )
        app.method_var = type(
            "Variable", (), {"get": lambda self: "centerline_gaussian"}
        )()
        self.assertEqual(
            [item[0] for item in app._settings_for_method()],
            [
                "perpendicular_width",
                "length",
                "savgol_window",
                "intensity_savgol_window",
            ],
        )

    def test_plate_validation_reports_missing_and_valid_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plate = pathlib.Path(temporary) / "plate_001"
            plate.mkdir()
            missing = gui.validate_folder(plate)
            self.assertFalse(missing.valid)
            self.assertEqual(missing.state, "inconsistent")
            write_info(plate / "info.xlsx")
            write_referenced_image(plate)
            valid = gui.validate_folder(plate)
            self.assertTrue(valid.valid)
            self.assertEqual(valid.message.split(" — ")[0], "Valid plate")

    def test_plate_validation_flags_inconsistent_date_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plate = pathlib.Path(temporary) / "plate_001"
            plate.mkdir()
            write_info(plate / "info.xlsx")
            (plate / "date_20260102").mkdir()
            result = gui.validate_folder(plate)
            self.assertFalse(result.valid)
            self.assertEqual(result.state, "inconsistent")
            self.assertIn("inconsistent", result.message)
            self.assertIn("missing date folders: 20260101", result.details)

    def test_plate_validation_matches_analysis_date_folder_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plate = pathlib.Path(temporary) / "plate_001"
            plate.mkdir()
            write_info(plate / "info.xlsx")
            nested_date = plate / "images" / "date_20260101_control"
            nested_date.mkdir(parents=True)
            image = nested_date / "row_001" / "fluo_1_1.czi"
            image.parent.mkdir()
            image.write_bytes(b"test image placeholder")
            result = gui.validate_folder(plate)
            self.assertTrue(result.valid)

    def test_experiment_validation_keeps_the_plate_failure_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            experiment = pathlib.Path(temporary) / "experiment_001"
            plate = experiment / "plate_001"
            plate.mkdir(parents=True)
            write_info(plate / "info.xlsx")
            (plate / "date_20260102").mkdir()
            result = gui.validate_folder(experiment)
            self.assertFalse(result.valid)
            self.assertIn("plate_001", result.details[0])
            self.assertIn("missing date folders: 20260101", result.details[0])
            tooltip = gui.validation_tooltip_text(result)
            self.assertIn("Details:", tooltip)
            self.assertIn("missing date folders: 20260101", tooltip)
            self.assertIn("unexpected date folders: 20260102", tooltip)
            display = gui.validation_display_text(result)
            self.assertIn("hover for more information", display)
            self.assertNotIn("missing date folders: 20260101", display)

    def test_calibration_states_distinguish_plate_experiment_and_manual(self) -> None:
        shell = object.__new__(gui.AnalyzeApp)
        with tempfile.TemporaryDirectory() as temporary:
            experiment = pathlib.Path(temporary) / "experiment_001"
            plate = experiment / "plate_001"
            plate.mkdir(parents=True)
            (experiment / "cal.txt").write_text("0.25\n", encoding="utf-8")
            value, editable, source, error = shell.resolve_calibration(plate)
            self.assertEqual(
                (value, editable, source, error),
                (0.25, False, "Loaded from experiment", ""),
            )
            (plate / "cal.txt").write_text("0.5\n", encoding="utf-8")
            value, editable, source, _error = shell.resolve_calibration(plate)
            self.assertEqual(
                (value, editable, source), (0.5, False, "Loaded from plate")
            )
            (plate / "cal.txt").write_text("not-a-number\n", encoding="utf-8")
            _value, editable, source, error = shell.resolve_calibration(plate)
            self.assertTrue(editable)
            self.assertEqual(source, "Manual value")
            self.assertIn("Invalid cal.txt", error)

    def test_run_discovery_ignores_legacy_output_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = pathlib.Path(temporary) / "plate_001"
            folder.mkdir()
            (folder / "source.czi").write_text("input", encoding="utf-8")
            (folder / "_output_summary.pdf").write_text("output", encoding="utf-8")
            self.assertEqual(gui.workflow.discover_runs(folder), [])

    def test_completion_feedback_is_successful_or_actionable(self) -> None:
        self.assertEqual(
            gui.completion_feedback(True, 0),
            ("Analysis completed successfully.", "success"),
        )
        text, kind = gui.completion_feedback(False, 2)
        self.assertEqual(kind, "warning")
        self.assertIn("2 image or processing errors", text)


class InfoTemplateGuiTests(unittest.TestCase):
    def shell(self) -> gui.AnalyzeApp:
        """Use the real decision methods without constructing a Tk window."""
        app = object.__new__(gui.AnalyzeApp)
        app.is_busy = False
        app.templates_in_progress = True
        app.closing = False
        app.template_generation = 1
        app.template_cancel_event = threading.Event()
        app.winfo_exists = mock.Mock(return_value=True)
        app.template_status_var = mock.Mock()
        app.template_details_var = mock.Mock()
        app.template_details_button = mock.Mock()
        app.config = mock.Mock()
        app.set_config_state = mock.Mock()
        app.footer = mock.Mock()
        app.update_action_buttons_state = mock.Mock()
        # Run worker callbacks immediately to make decisions observable.
        app._post_template_callback = lambda callback, *args: callback(*args)
        return app

    def test_button_available_without_info_but_blocked_during_other_work(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            for name in ("plate_001", "experiment_001", "other"):
                (root / name).mkdir()
            self.assertTrue(gui.can_build_info_templates(root / "plate_001"))
            self.assertTrue(gui.can_build_info_templates(root / "experiment_001"))
            self.assertFalse(
                gui.can_build_info_templates(root / "plate_001", busy=True)
            )
            self.assertFalse(gui.can_build_info_templates(root / "other"))
            self.assertFalse(gui.can_build_info_templates(root / "plate_missing"))
            self.assertFalse(gui.can_build_info_templates(None))

    def test_declining_overwrite_writes_nothing_and_restores_controls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plate = pathlib.Path(temporary)
            template = gui.workflow.InfoTemplate(plate, ("20260101",), ())
            template.path.write_bytes(b"edited template")
            app = self.shell()
            with (
                mock.patch.object(
                    gui.messagebox, "askyesno", return_value=False
                ) as ask,
                mock.patch.object(gui.workflow, "write_info_templates") as write,
                mock.patch.object(gui.threading, "Thread") as thread,
            ):
                app._finish_template_scan(1, gui.workflow.InfoTemplateScan((template,)))
            ask.assert_called_once()
            self.assertIn(str(template.path), ask.call_args.args[1])
            write.assert_not_called()
            thread.assert_not_called()
            self.assertEqual(template.path.read_bytes(), b"edited template")
            self.assertFalse(app.templates_in_progress)
            app.set_config_state.assert_called_once_with(True)
            app.footer.hide_progress.assert_called_once()
            self.assertIn(
                "No files were written", app.template_status_var.set.call_args.args[0]
            )

    def test_approval_covers_only_existing_destinations_and_success_lists_paths(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            templates = tuple(
                gui.workflow.InfoTemplate(root / name, ("20260101",), ())
                for name in ("plate_001", "plate_002")
            )
            templates[0].plate.mkdir()
            templates[0].path.write_bytes(b"old template")
            app = self.shell()
            result = gui.workflow.InfoTemplateResult(
                paths=tuple(template.path for template in templates),
                unresolved_images=2,
            )
            with (
                mock.patch.object(gui.messagebox, "askyesno", return_value=True),
                mock.patch.object(
                    gui.workflow, "write_info_templates", return_value=result
                ) as write,
                mock.patch.object(gui.threading, "Thread") as thread,
            ):
                thread.side_effect = lambda *, target, daemon: type(
                    "ImmediateThread", (), {"start": lambda self: target()}
                )()
                app._finish_template_scan(1, gui.workflow.InfoTemplateScan(templates))
            self.assertEqual(
                write.call_args.kwargs["overwrite_paths"],
                frozenset({templates[0].path}),
            )
            self.assertFalse(app.templates_in_progress)
            details = app.template_details_var.set.call_args.args[0]
            self.assertTrue(
                all(str(template.path) in details for template in templates)
            )
            self.assertIn(
                "2 image(s) need positions",
                app.template_status_var.set.call_args.args[0],
            )

    def test_write_failure_and_empty_scan_restore_controls(self) -> None:
        app = self.shell()
        template = gui.workflow.InfoTemplate(
            pathlib.Path("plate_001"), ("20260101",), ()
        )
        with (
            mock.patch.object(
                gui.workflow, "write_info_templates", side_effect=OSError("disk full")
            ),
            mock.patch.object(gui.threading, "Thread") as thread,
        ):
            thread.side_effect = lambda *, target, daemon: type(
                "ImmediateThread", (), {"start": lambda self: target()}
            )()
            app._finish_template_scan(1, gui.workflow.InfoTemplateScan((template,)))
        self.assertFalse(app.templates_in_progress)
        self.assertIn("disk full", app.template_details_var.set.call_args.args[0])
        app.templates_in_progress = True
        app._finish_template_scan(
            1, gui.workflow.InfoTemplateScan(errors=("plate_001: no root images",))
        )
        self.assertFalse(app.templates_in_progress)
        self.assertIn("no root images", app.template_details_var.set.call_args.args[0])

    def test_scan_failure_restores_controls_and_stale_callbacks_are_ignored(
        self,
    ) -> None:
        app = self.shell()
        with tempfile.TemporaryDirectory() as temporary:
            plate = pathlib.Path(temporary) / "plate_001"
            plate.mkdir()
            app.templates_in_progress = False
            app.selected_folder_var = mock.Mock()
            app.selected_folder_var.get.return_value = str(plate)
            app.folder_check_in_progress = False
            app.audit_in_progress = False
            app.path_validation_after = None
            app.validation_generation = 0
            app.template_details_label = mock.Mock()
            with (
                mock.patch.object(
                    gui.workflow,
                    "scan_info_templates",
                    side_effect=OSError("cannot read folder"),
                ),
                mock.patch.object(gui.threading, "Thread") as thread,
            ):
                thread.side_effect = lambda *, target, daemon: type(
                    "ImmediateThread", (), {"start": lambda self: target()}
                )()
                app.build_info_templates()
        self.assertFalse(app.templates_in_progress)
        self.assertIn(
            "cannot read folder", app.template_details_var.set.call_args.args[0]
        )
        app.template_status_var.reset_mock()
        app._finish_info_templates(
            1, gui.workflow.InfoTemplateScan(), gui.workflow.InfoTemplateResult()
        )
        app.template_status_var.set.assert_not_called()
        app.closing = True
        app._finish_info_templates(
            2, gui.workflow.InfoTemplateScan(), gui.workflow.InfoTemplateResult()
        )
        app.template_status_var.set.assert_not_called()


if __name__ == "__main__":
    unittest.main()
