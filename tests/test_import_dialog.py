from __future__ import annotations

import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from knife_edge_app import ImportDialog


class ImportDialogTests(unittest.TestCase):
    def test_excel_rows_are_numbered_and_directly_selected(self) -> None:
        temporary = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
        source = Path(temporary.name)
        temporary.close()
        source.unlink()
        pd.DataFrame(
            [
                ["measurement notes", ""],
                ["X", "Power"],
                [0.0, 0.1],
                [0.5, 0.4],
                [1.0, 1.2],
                [1.5, 2.1],
                [2.0, 2.4],
                [2.5, 2.5],
            ]
        ).to_excel(source, header=False, index=False)

        try:
            try:
                root = tk.Tk()
            except tk.TclError as exc:
                self.skipTest(f"Tk is unavailable: {exc}")
            root.withdraw()
            dialog = ImportDialog(root, source)
            dialog.withdraw()

            rows = list(dialog.preview.get_children())
            self.assertEqual(len(rows), 8)
            self.assertEqual(dialog.preview.heading("#0", "text"), "Row")
            self.assertEqual(dialog.preview.item(rows[0], "text"), "1")
            self.assertEqual(dialog.preview.item(rows[-1], "text"), "8")

            dialog.preview.selection_set(rows[2:8])
            dialog._update_selection_summary()
            self.assertEqual(dialog.selected_rows_var.get(), "3–8 (6 rows)")
            dialog.x_column_var.set("A")
            dialog.signal_column_var.set("B")
            dialog._accept()

            self.assertIsNotNone(dialog.result)
            assert dialog.result is not None
            x, signal, coordinate_name, signal_name = dialog.result
            np.testing.assert_allclose(x, [0.0, 0.5, 1.0, 1.5, 2.0, 2.5])
            np.testing.assert_allclose(signal, [0.1, 0.4, 1.2, 2.1, 2.4, 2.5])
            self.assertEqual(coordinate_name, "A")
            self.assertEqual(signal_name, "B")
            root.destroy()
        finally:
            source.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()

