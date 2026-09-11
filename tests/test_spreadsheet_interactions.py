from __future__ import annotations

import sys
import tkinter as tk
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from knife_edge_app import EditableDataTable


class SpreadsheetInteractionTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk is unavailable: {exc}")
        self.root.geometry("500x420+0+0")
        self.table = EditableDataTable(self.root, rows=10)
        self.table.pack(fill="both", expand=True)
        self.root.update_idletasks()
        self.root.attributes("-alpha", 0.0)

    def tearDown(self) -> None:
        if hasattr(self, "root"):
            self.root.update_idletasks()
            self.root.destroy()

    def test_fill_handle_extends_an_arithmetic_series(self) -> None:
        rows = list(self.table.tree.get_children())
        self.table.tree.item(rows[0], values=("0", ""))
        self.table.tree.item(rows[1], values=("1", ""))
        self.table._active_column = 0
        self.table.tree.selection_set(rows[:2])
        self.table._fill_source = (0, 1)
        self.table._fill_target = 6

        self.table._finish_fill(None)

        values = [self.table.tree.item(row, "values")[0] for row in rows[:7]]
        self.assertEqual(values, ["0", "1", "2", "3", "4", "5", "6"])

    def test_enter_commits_and_opens_the_next_row(self) -> None:
        rows = list(self.table.tree.get_children())
        self.table._open_cell(rows[0], 1, replace_text="12.5")

        self.table._commit_and_move(type("Event", (), {})())

        self.assertEqual(self.table.tree.item(rows[0], "values")[1], "12.5")
        self.assertEqual(self.table._editing_item, rows[1])
        self.assertEqual(self.table._editing_column, 1)
        self.table._cancel_edit()

    def test_headers_are_user_configurable(self) -> None:
        self.table.set_headers("Y", "Photocurrent")

        self.assertEqual(self.table.tree.heading("position", "text"), "Y")
        self.assertEqual(self.table.tree.heading("signal", "text"), "Photocurrent")


if __name__ == "__main__":
    unittest.main()
