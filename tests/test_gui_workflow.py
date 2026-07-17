"""Workflow-level checks for the desktop UI's non-visual decision logic."""

import importlib.util
import pathlib
import sys
import tempfile
import unittest

from openpyxl import Workbook


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


class WorkflowValidationTests(unittest.TestCase):
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
            (plate / "date_20260101").mkdir()
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
            self.assertIn("info.xlsx was found", result.message)
            self.assertIn("missing date folders: 20260101", result.details[0])

    def test_plate_validation_matches_analysis_date_folder_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plate = pathlib.Path(temporary) / "plate_001"
            plate.mkdir()
            write_info(plate / "info.xlsx")
            nested_date = plate / "images" / "date_20260101_control"
            nested_date.mkdir(parents=True)
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

    def test_delete_confirmation_lists_only_generated_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = pathlib.Path(temporary) / "plate_001"
            folder.mkdir()
            (folder / "source.czi").write_text("input", encoding="utf-8")
            (folder / "_output_summary.pdf").write_text("output", encoding="utf-8")
            shell = object.__new__(gui.AnalyzeApp)
            shell.selected_folder = folder
            message = shell.deletion_message()
            self.assertIn("_output_summary.pdf", message)
            self.assertNotIn("source.czi", message)

    def test_completion_feedback_is_successful_or_actionable(self) -> None:
        self.assertEqual(
            gui.completion_feedback(True, 0),
            ("Analysis completed successfully.", "success"),
        )
        text, kind = gui.completion_feedback(False, 2)
        self.assertEqual(kind, "warning")
        self.assertIn("2 image or processing errors", text)


if __name__ == "__main__":
    unittest.main()
