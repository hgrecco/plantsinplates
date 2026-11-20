"""Folder scanning and safe publication of editable plate workbooks."""

import pathlib
import tempfile
import threading
import unittest
from unittest import mock

from openpyxl import Workbook, load_workbook

from plantsinplates import workflow


class InfoTemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = pathlib.Path(temporary.name).resolve()

    def image(self, relative: str) -> pathlib.Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"image contents are not read when making templates")
        return path

    def test_positions_merge_across_dates_and_workbook_has_editable_blanks(
        self,
    ) -> None:
        self.image("plate_001/date_20260101_control/row_001/fluo_001_02.czi")
        self.image("plate_001/images/date_20260102.0930_after/FLUO_1_2.CZI")
        self.image("plate_001/date_20260101_control/fluo_10_3.png")
        plate = self.root / "plate_001"
        scan = workflow.scan_info_templates(plate)
        self.assertEqual(scan.errors, ())
        template = scan.templates[0]
        self.assertEqual(
            template.rows,
            (
                (1, 2, None, None, "row_001/fluo_001_02.czi", None, "FLUO_1_2.CZI"),
                (10, 3, None, None, "fluo_10_3.png", None, None),
            ),
        )
        result = workflow.write_info_templates(scan.templates)
        self.assertEqual(result.errors, ())
        self.assertEqual(result.paths, (plate / "info_template.xlsx",))
        workbook = load_workbook(result.paths[0])
        self.addCleanup(workbook.close)
        sheet = workbook.worksheets[0]
        rows = list(sheet.values)
        self.assertEqual(
            rows[0],
            (
                "row",
                "col",
                "genotype",
                "20260101_length",
                "20260101_fluo",
                "20260102.0930_length",
                "20260102.0930_fluo",
            ),
        )
        self.assertEqual(rows[1:], list(template.rows))
        self.assertEqual(sheet.freeze_panes, "A2")
        self.assertEqual(sheet.auto_filter.ref, "A1:G3")
        self.assertFalse((plate / "info.xlsx").exists())

        # After the user fills the supplied fields, the generated paths satisfy
        # the real folder contract without reading or measuring image contents.
        for row in sheet.iter_rows(min_row=2):
            row[2].value = "control"
            row[3].value = 12.0
            if row[6].value:
                row[5].value = 13.0
        workbook.save(plate / "info.xlsx")
        validation = workflow.validate_folder(plate)
        self.assertTrue(validation.valid, validation.details)
        self.assertEqual(validation.image_count, 3)

    def test_unknown_images_stay_separate_even_with_identical_relative_paths(
        self,
    ) -> None:
        for date in ("20260101", "20260102"):
            self.image(f"plate_001/date_{date}/row_001/unknown.png")
        scan = workflow.scan_info_templates(self.root / "plate_001")
        template = scan.templates[0]
        self.assertEqual(
            template.rows,
            (
                (None, None, None, None, "row_001/unknown.png", None, None),
                (None, None, None, None, None, None, "row_001/unknown.png"),
            ),
        )
        self.assertEqual(len(template.unresolved_images), 2)

    def test_conflicting_images_are_preserved_with_unresolved_positions(self) -> None:
        for relative in (
            "plate_001/date_20260101/first/fluo_1_1.czi",
            "plate_001/date_20260101/second/fluo_01_01.png",
            "plate_001/date_20260102/fluo_1_1.czi",
        ):
            self.image(relative)
        template = workflow.scan_info_templates(self.root / "plate_001").templates[0]
        self.assertEqual(len(template.rows), 3)
        self.assertEqual(
            template.rows[0], (1, 1, None, None, None, None, "fluo_1_1.czi")
        )
        self.assertTrue(all(row[:3] == (None, None, None) for row in template.rows[1:]))
        self.assertEqual(len(template.unresolved_images), 2)
        self.assertIn("multiple images for row 1, col 1", template.warnings[0])

    def test_supported_extensions_and_managed_overview_hidden_exclusions(self) -> None:
        for index, suffix in enumerate(sorted(workflow.IMAGE_SUFFIXES), 1):
            self.image(f"plate_001/date_20260101/fluo_1_{index}{suffix.upper()}")
        for relative in (
            "plate_001/date_20260101/overview.png",
            "plate_001/date_20260101/Overview_plate.CZI",
            "plate_001/date_20260101/.fluo_1_90.png",
            "plate_001/date_20260101/not_an_image.txt",
            "plate_001/date_20260101/_output_previous/fluo_1_91.png",
            "plate_001/date_20260101/.plantsinplates_cache/fluo_1_92.png",
            "plate_001/_output_previous/date_20260102/fluo_1_93.png",
            "plate_001/.plantsinplates_cache/date_20260103/fluo_1_94.png",
            "plate_001/fluo_1_95.png",
        ):
            self.image(relative)
        template = workflow.scan_info_templates(self.root / "plate_001").templates[0]
        self.assertEqual(template.dates, ("20260101",))
        self.assertEqual(len(template.rows), len(workflow.IMAGE_SUFFIXES))
        self.assertEqual(template.unresolved_images, ())

    def test_nested_date_images_belong_to_their_own_date(self) -> None:
        self.image("plate_001/date_20260101/fluo_1_1.czi")
        self.image("plate_001/date_20260101/nested/date_20260102/fluo_1_1.czi")
        template = workflow.scan_info_templates(self.root / "plate_001").templates[0]
        self.assertEqual(
            template.rows, ((1, 1, None, None, "fluo_1_1.czi", None, "fluo_1_1.czi"),)
        )

    def test_experiment_reports_bad_plates_and_continues_with_usable_ones(self) -> None:
        self.image("experiment_001/plate_001/date_20260101/fluo_1_1.png")
        self.image("experiment_001/plate_002/date_20260101/fluo_1_1.png")
        self.image("experiment_001/plate_002/nested/date_20260101_control/fluo_1_1.png")
        (self.root / "experiment_001/plate_003").mkdir()
        self.image("experiment_001/plate_004/date_20260101/overview.png")
        self.image("experiment_001/other/plate_005/date_20260101/fluo_1_1.png")
        scan = workflow.scan_info_templates(self.root / "experiment_001")
        self.assertEqual(
            [template.plate.name for template in scan.templates], ["plate_001"]
        )
        self.assertEqual(len(scan.errors), 3)
        self.assertIn("multiple date folders", scan.errors[0])
        self.assertIn("no usable date_", scan.errors[1])
        self.assertIn("no root images", scan.errors[2])
        result = workflow.write_info_templates(scan.templates)
        self.assertEqual(len(result.paths), 1)

    def test_invalid_selection_and_empty_experiment_are_actionable(self) -> None:
        for name, message in (
            ("other", "Choose an existing"),
            ("experiment_001", "No plate_"),
        ):
            folder = self.root / name
            folder.mkdir()
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, message):
                workflow.scan_info_templates(folder)

    def test_existing_workbooks_are_preserved_until_template_replacement_is_approved(
        self,
    ) -> None:
        self.image("plate_001/date_20260101/fluo_1_1.png")
        plate = self.root / "plate_001"
        info = plate / "info.xlsx"
        info.write_bytes(b"original input workbook")
        scan = workflow.scan_info_templates(plate)
        target = plate / "info_template.xlsx"
        target.write_bytes(b"user-edited template")
        refused = workflow.write_info_templates(scan.templates)
        self.assertEqual(refused.paths, ())
        self.assertEqual(len(refused.errors), 1)
        self.assertEqual(target.read_bytes(), b"user-edited template")
        replaced = workflow.write_info_templates(
            scan.templates, overwrite_paths=frozenset({target})
        )
        self.assertEqual(replaced.paths, (target,))
        self.assertEqual(replaced.errors, ())
        self.assertEqual(info.read_bytes(), b"original input workbook")
        self.assertEqual(list(plate.glob(".info-template-*")), [])

    def test_destination_appearing_during_write_is_not_replaced(self) -> None:
        self.image("plate_001/date_20260101/fluo_1_1.png")
        scan = workflow.scan_info_templates(self.root / "plate_001")
        target = scan.templates[0].path
        save = Workbook.save

        def save_and_create_destination(
            workbook: Workbook, filename: pathlib.Path
        ) -> None:
            save(workbook, filename)
            target.write_bytes(b"another user's new template")

        with mock.patch.object(
            Workbook, "save", autospec=True, side_effect=save_and_create_destination
        ):
            result = workflow.write_info_templates(scan.templates)
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(target.read_bytes(), b"another user's new template")

    def test_failed_save_preserves_existing_template_and_removes_temporary_file(
        self,
    ) -> None:
        self.image("plate_001/date_20260101/fluo_1_1.png")
        scan = workflow.scan_info_templates(self.root / "plate_001")
        target = scan.templates[0].path
        target.write_bytes(b"keep this template")
        with mock.patch.object(
            Workbook, "save", side_effect=PermissionError("disk unavailable")
        ):
            result = workflow.write_info_templates(
                scan.templates, overwrite_paths=frozenset({target})
            )
        self.assertIn("disk unavailable", result.errors[0])
        self.assertEqual(target.read_bytes(), b"keep this template")
        self.assertEqual(list(target.parent.glob(".info-template-*")), [])

    def test_cancellation_stops_scan_and_keeps_only_completed_templates(self) -> None:
        for plate in ("plate_001", "plate_002"):
            self.image(f"experiment_001/{plate}/date_20260101/fluo_1_1.png")
        experiment = self.root / "experiment_001"
        cancellation = threading.Event()
        with self.assertRaises(workflow.AnalysisCancelled):
            workflow.scan_info_templates(
                experiment,
                cancel_event=cancellation,
                progress=lambda _event: cancellation.set(),
            )
        self.assertEqual(list(experiment.rglob("info_template.xlsx")), [])
        cancellation.clear()
        scan = workflow.scan_info_templates(experiment)
        events: list[workflow.ProgressEvent] = []

        def cancel_after_first(event: workflow.ProgressEvent) -> None:
            events.append(event)
            cancellation.set()

        result = workflow.write_info_templates(
            scan.templates, cancel_event=cancellation, progress=cancel_after_first
        )
        self.assertTrue(result.cancelled)
        self.assertEqual(result.paths, (scan.templates[0].path,))
        self.assertFalse(scan.templates[1].path.exists())
        self.assertEqual(events[0].stage, "templates")


if __name__ == "__main__":
    unittest.main()
