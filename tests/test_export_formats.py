from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from knife_edge_app import _write_table_data


class ExportFormatTests(unittest.TestCase):
    def test_txt_export_is_tab_delimited_and_round_trips(self) -> None:
        temporary = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
        path = Path(temporary.name)
        temporary.close()
        frame = pd.DataFrame({"Y": [0.0, 0.5, 1.0], "Photocurrent": [12.0, 18.0, 31.0]})
        metadata = pd.DataFrame({"field": ["position_unit"], "value": ["mm"]})

        try:
            _write_table_data(path, frame, metadata)

            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("Y\tPhotocurrent\n"))
            imported = pd.read_csv(path, sep="\t")
            pd.testing.assert_frame_equal(imported, frame)
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()

