from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
from scipy.special import erf


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from knife_edge_core import FWHM_FACTOR, KnifeEdgeError, analyze_knife_edge, prepare_data


def synthetic_step(
    *, sigma: float = 0.22, center: float = 0.17, height: float = 3.4, noise: float = 0.0
) -> tuple[np.ndarray, np.ndarray]:
    x = np.linspace(-1.5, 1.7, 241)
    signal = 0.35 + 0.5 * height * (
        1 + erf((x - center) / (np.sqrt(2) * sigma))
    )
    if noise:
        signal += np.random.default_rng(20260909).normal(0, noise, x.size)
    return x, signal


class KnifeEdgeCoreTests(unittest.TestCase):
    def test_erf_fit_recovers_fwhm_center_and_sign(self) -> None:
        sigma = 0.22
        center = 0.17
        x, signal = synthetic_step(sigma=sigma, center=center, height=-3.4, noise=0.006)

        result = analyze_knife_edge(x, signal, method="erf")

        self.assertAlmostEqual(result.fwhm, FWHM_FACTOR * sigma, delta=FWHM_FACTOR * sigma * 0.01)
        self.assertAlmostEqual(result.center, center, delta=0.004)
        self.assertLess(result.parameters["height"], 0)
        self.assertGreater(result.r_squared, 0.999)
        self.assertIsNotNone(result.signal_fit)
        self.assertEqual(len(result.fit_x), 1000)

    def test_derivative_fit_recovers_gaussian_width(self) -> None:
        sigma = 0.18
        x, signal = synthetic_step(sigma=sigma, noise=0.0)

        result = analyze_knife_edge(x, signal, method="derivative")

        self.assertAlmostEqual(result.fwhm, FWHM_FACTOR * sigma, delta=FWHM_FACTOR * sigma * 0.01)
        self.assertAlmostEqual(result.parameters["sigma"], sigma, delta=sigma * 0.01)
        self.assertGreater(result.r_squared, 0.9999)
        self.assertIsNone(result.signal_fit)

    def test_prepare_data_sorts_removes_invalid_and_averages_duplicates(self) -> None:
        x, signal = prepare_data(
            np.array([3, 1, 2, 2, np.nan, 5, 4], dtype=float),
            np.array([30, 10, 18, 22, 999, 50, 40], dtype=float),
        )

        np.testing.assert_allclose(x, [1, 2, 3, 4, 5])
        np.testing.assert_allclose(signal, [10, 20, 30, 40, 50])

    def test_rejects_too_few_and_constant_signal(self) -> None:
        with self.assertRaisesRegex(KnifeEdgeError, "five"):
            analyze_knife_edge(np.arange(4), np.arange(4), method="erf")
        with self.assertRaisesRegex(KnifeEdgeError, "constant"):
            analyze_knife_edge(np.arange(6), np.ones(6), method="erf")

    def test_result_summary_contains_export_fields(self) -> None:
        x, signal = synthetic_step()
        result = analyze_knife_edge(x, signal, method="erf")
        rows = result.summary_rows("mm")
        quantities = {row["quantity"] for row in rows}

        self.assertTrue({"method", "center", "sigma", "FWHM", "R_squared"} <= quantities)
        fwhm_row = next(row for row in rows if row["quantity"] == "FWHM")
        self.assertEqual(fwhm_row["unit"], "mm")


if __name__ == "__main__":
    unittest.main()
