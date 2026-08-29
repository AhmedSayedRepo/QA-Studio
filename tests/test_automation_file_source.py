import csv
import tempfile
import unittest
from pathlib import Path

import automation_file_source as source


class AutomationFileSourceTests(unittest.TestCase):
    def _csv(self, folder, rows):
        path = Path(folder) / "cases.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            fields = ["Test Case ID", "Test Case Title", "Action", "Expected Result"]
            fields.extend(key for row in rows for key in row if key not in fields)
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_csv_groups_steps_and_preserves_authored_order(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._csv(folder, [
                {"Test Case ID": "101", "Test Case Title": "Add backpack",
                 "Action": "Log in", "Expected Result": "Products page opens"},
                {"Test Case ID": "101", "Test Case Title": "Add backpack",
                 "Action": "Add the backpack", "Expected Result": "Cart badge is 1"},
                {"Test Case ID": "102", "Test Case Title": "Remove backpack",
                 "Action": "Open the cart", "Expected Result": "Backpack is listed"},
            ])
            cases = source.load_test_cases(path)

        self.assertEqual([case["title"] for case in cases],
                         ["Add backpack", "Remove backpack"])
        self.assertEqual([step["action"] for step in cases[0]["steps"]],
                         ["Log in", "Add the backpack"])
        self.assertEqual([step["index"] for step in cases[0]["steps"]], [1, 2])

    def test_payload_has_stable_non_azure_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._csv(folder, [
                {"Test Case ID": "1", "Test Case Title": "Sample",
                 "Action": "Open the site", "Expected Result": "Page opens"},
            ])
            first = source.load_stories_payload(path)
            second = source.load_stories_payload(path)

        self.assertEqual(first[0]["story"]["id"], second[0]["story"]["id"])
        self.assertTrue(first[0]["story"]["id"].startswith("file-"))
        self.assertTrue(first[0]["test_cases"][0]["id"].startswith("file-"))

    def test_xlsx_is_read_with_the_same_column_contract(self):
        from openpyxl import Workbook
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cases.xlsx"
            book = Workbook()
            sheet = book.active
            sheet.append(["Test Case Title", "Action", "Expected Result"])
            sheet.append(["Sign in", "Enter valid credentials", "Products are visible"])
            book.save(path)
            book.close()
            cases = source.load_test_cases(path)

        self.assertEqual(cases[0]["title"], "Sign in")
        self.assertEqual(cases[0]["steps"][0]["expected"], "Products are visible")

    def test_rejects_rows_without_required_action(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._csv(folder, [
                {"Test Case ID": "1", "Test Case Title": "Sample",
                 "Action": "", "Expected Result": "Page opens"},
            ])
            with self.assertRaisesRegex(ValueError, "Action is required"):
                source.load_test_cases(path)

    def test_preserves_reviewed_browser_operation_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._csv(folder, [{
                "Test Case ID": "1", "Test Case Title": "Frame upload",
                "Action": "Switch to the editor frame", "Expected Result": "",
                "Element Type": "Iframe", "Preferred Locator": "iframe#editor",
                "Frame Context": "editor", "Automation Note": "switch context",
            }])
            step = source.load_test_cases(path)[0]["steps"][0]

        self.assertEqual(step["element_type"], "Iframe")
        self.assertEqual(step["preferred_locator"], "iframe#editor")
        self.assertEqual(step["frame_context"], "editor")


if __name__ == "__main__":
    unittest.main()
