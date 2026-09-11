"""Numerical analysis for knife-edge beam-size measurements.

The two fitting paths mirror the original ``knife_edge.ipynb`` notebook:

* ``erf`` fits the measured knife-edge step directly.
* ``derivative`` differentiates the step and fits the absolute derivative to a
  Gaussian profile.

The module deliberately contains no GUI code so that the analysis can be
tested and reused from scripts or notebooks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from scipy.optimize import curve_fit
from scipy.special import erf


Method = Literal["erf", "derivative"]
FWHM_FACTOR = 2.0 * np.sqrt(2.0 * np.log(2.0))


class KnifeEdgeError(ValueError):
    """Raised when knife-edge data cannot be analyzed."""


@dataclass(frozen=True)
class KnifeEdgeResult:
    """Complete fitted result, including arrays needed for plots and export."""

    method: Method
    x: np.ndarray
    signal: np.ndarray
    fit_x: np.ndarray
    signal_fit: np.ndarray | None
    profile_x: np.ndarray
    profile: np.ndarray
    profile_fit: np.ndarray
    residual_x: np.ndarray
    residual: np.ndarray
    parameters: dict[str, float]
    uncertainties: dict[str, float]
    fwhm: float
    fwhm_error: float
    r_squared: float

    @property
    def center(self) -> float:
        return self.parameters["center"]

    @property
    def sigma(self) -> float:
        return abs(self.parameters["sigma"])

    def summary_rows(self, x_unit: str = "") -> list[dict[str, object]]:
        """Return a tabular representation suitable for CSV/Excel export."""

        rows: list[dict[str, object]] = [
            {
                "quantity": "method",
                "value": self.method,
                "uncertainty": "",
                "unit": "",
            }
        ]
        parameter_units = {
            "offset": "signal",
            "height": "signal",
            "amplitude": "signal/x",
            "center": x_unit,
            "sigma": x_unit,
            "profile_offset": "signal/x",
        }
        for name, value in self.parameters.items():
            rows.append(
                {
                    "quantity": name,
                    "value": value,
                    "uncertainty": self.uncertainties.get(name, np.nan),
                    "unit": parameter_units.get(name, ""),
                }
            )
        rows.extend(
            [
                {
                    "quantity": "FWHM",
                    "value": self.fwhm,
                    "uncertainty": self.fwhm_error,
                    "unit": x_unit,
                },
                {
                    "quantity": "R_squared",
                    "value": self.r_squared,
                    "uncertainty": "",
                    "unit": "",
                },
            ]
        )
        return rows


def erf_step(x: np.ndarray, offset: float, height: float, x0: float, sigma: float) -> np.ndarray:
    """Integrated Gaussian used to model a raw knife-edge trace."""

    return offset + 0.5 * height * (
        1.0 + erf((x - x0) / (np.sqrt(2.0) * sigma))
    )


def gaussian_profile(
    x: np.ndarray, amplitude: float, x0: float, sigma: float, offset: float
) -> np.ndarray:
    """Gaussian profile used for the differentiated knife-edge trace."""

    return amplitude * np.exp(-((x - x0) ** 2) / (2.0 * sigma**2)) + offset


def prepare_data(x: np.ndarray, signal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Convert, clean and sort input; average signals at duplicate positions."""

    try:
        x_array = np.asarray(x, dtype=float).reshape(-1)
        signal_array = np.asarray(signal, dtype=float).reshape(-1)
    except (TypeError, ValueError) as exc:
        raise KnifeEdgeError("Position and signal values must be numeric.") from exc

    if x_array.size != signal_array.size:
        raise KnifeEdgeError("Position and signal columns must have the same length.")

    finite = np.isfinite(x_array) & np.isfinite(signal_array)
    x_array = x_array[finite]
    signal_array = signal_array[finite]
    if x_array.size < 5:
        raise KnifeEdgeError("At least five valid position/signal pairs are required.")

    order = np.argsort(x_array, kind="stable")
    x_array = x_array[order]
    signal_array = signal_array[order]

    unique_x, inverse, counts = np.unique(x_array, return_inverse=True, return_counts=True)
    if unique_x.size != x_array.size:
        sums = np.zeros(unique_x.size, dtype=float)
        np.add.at(sums, inverse, signal_array)
        signal_array = sums / counts
        x_array = unique_x

    if x_array.size < 5:
        raise KnifeEdgeError("At least five unique knife positions are required.")
    if np.ptp(x_array) <= 0:
        raise KnifeEdgeError("Knife positions must span a non-zero range.")
    if np.ptp(signal_array) <= np.finfo(float).eps:
        raise KnifeEdgeError("The signal is constant; no knife edge can be fitted.")

    return x_array, signal_array


def _parameter_errors(covariance: np.ndarray, count: int) -> np.ndarray:
    diagonal = np.diag(covariance)
    with np.errstate(invalid="ignore"):
        errors = np.sqrt(np.maximum(diagonal, 0.0))
    if errors.size != count:
        return np.full(count, np.nan)
    return errors


def _r_squared(observed: np.ndarray, fitted: np.ndarray) -> float:
    residual = observed - fitted
    ss_res = float(np.sum(residual**2))
    ss_tot = float(np.sum((observed - np.mean(observed)) ** 2))
    return float("nan") if ss_tot == 0 else 1.0 - ss_res / ss_tot


def fit_erf(x: np.ndarray, signal: np.ndarray) -> KnifeEdgeResult:
    """Fit the raw knife-edge step using the notebook's erf model."""

    x, signal = prepare_data(x, signal)
    edge_count = max(3, x.size // 10)
    edge_count = min(edge_count, max(1, x.size // 2))
    left_level = float(np.median(signal[:edge_count]))
    right_level = float(np.median(signal[-edge_count:]))
    height0 = right_level - left_level
    if abs(height0) <= np.finfo(float).eps:
        height0 = float(signal[-1] - signal[0])
    half_level = left_level + 0.5 * height0
    center0 = float(x[np.argmin(np.abs(signal - half_level))])

    span = float(np.ptp(x))
    spacing = np.diff(x)
    sigma_floor = max(float(np.min(spacing)) * 1e-3, span * 1e-9, 1e-15)
    sigma0 = max(span / 10.0, sigma_floor * 10.0)
    initial = [left_level, height0, center0, sigma0]
    lower = [-np.inf, -np.inf, float(x[0]), sigma_floor]
    upper = [np.inf, np.inf, float(x[-1]), span * 10.0]

    try:
        optimal, covariance = curve_fit(
            erf_step,
            x,
            signal,
            p0=initial,
            bounds=(lower, upper),
            maxfev=50_000,
        )
    except (RuntimeError, ValueError, FloatingPointError) as exc:
        raise KnifeEdgeError(f"Erf fit failed: {exc}") from exc

    errors = _parameter_errors(covariance, 4)
    offset, height, center, sigma = (float(value) for value in optimal)
    offset_error, height_error, center_error, sigma_error = (
        float(value) for value in errors
    )
    sigma = abs(sigma)

    fitted_at_data = erf_step(x, *optimal)
    residual = signal - fitted_at_data
    fit_x = np.linspace(float(x[0]), float(x[-1]), 1000)
    signal_fit = erf_step(fit_x, *optimal)
    profile_fit = abs(height) / (np.sqrt(2.0 * np.pi) * sigma) * np.exp(
        -((fit_x - center) ** 2) / (2.0 * sigma**2)
    )
    measured_profile = np.abs(np.gradient(signal, x))

    return KnifeEdgeResult(
        method="erf",
        x=x,
        signal=signal,
        fit_x=fit_x,
        signal_fit=signal_fit,
        profile_x=x,
        profile=measured_profile,
        profile_fit=profile_fit,
        residual_x=x,
        residual=residual,
        parameters={
            "offset": offset,
            "height": height,
            "center": center,
            "sigma": sigma,
        },
        uncertainties={
            "offset": offset_error,
            "height": height_error,
            "center": center_error,
            "sigma": sigma_error,
        },
        fwhm=float(FWHM_FACTOR * sigma),
        fwhm_error=float(FWHM_FACTOR * sigma_error),
        r_squared=_r_squared(signal, fitted_at_data),
    )


def fit_derivative(x: np.ndarray, signal: np.ndarray) -> KnifeEdgeResult:
    """Differentiate a knife-edge trace and fit its magnitude to a Gaussian."""

    x, signal = prepare_data(x, signal)
    profile = np.abs(np.gradient(signal, x))
    span = float(np.ptp(x))
    spacing = np.diff(x)
    sigma_floor = max(float(np.min(spacing)) * 1e-3, span * 1e-9, 1e-15)
    amplitude0 = float(np.max(profile) - np.min(profile))
    center0 = float(x[np.argmax(profile)])
    sigma0 = max(span / 10.0, sigma_floor * 10.0)
    offset0 = float(np.min(profile))

    try:
        optimal, covariance = curve_fit(
            gaussian_profile,
            x,
            profile,
            p0=[amplitude0, center0, sigma0, offset0],
            bounds=(
                [0.0, float(x[0]), sigma_floor, -np.inf],
                [np.inf, float(x[-1]), span * 10.0, np.inf],
            ),
            maxfev=50_000,
        )
    except (RuntimeError, ValueError, FloatingPointError) as exc:
        raise KnifeEdgeError(f"Gaussian fit failed: {exc}") from exc

    errors = _parameter_errors(covariance, 4)
    amplitude, center, sigma, offset = (float(value) for value in optimal)
    amplitude_error, center_error, sigma_error, offset_error = (
        float(value) for value in errors
    )
    sigma = abs(sigma)

    fitted_at_data = gaussian_profile(x, *optimal)
    residual = profile - fitted_at_data
    fit_x = np.linspace(float(x[0]), float(x[-1]), 1000)
    profile_fit = gaussian_profile(fit_x, *optimal)

    return KnifeEdgeResult(
        method="derivative",
        x=x,
        signal=signal,
        fit_x=fit_x,
        signal_fit=None,
        profile_x=x,
        profile=profile,
        profile_fit=profile_fit,
        residual_x=x,
        residual=residual,
        parameters={
            "amplitude": amplitude,
            "center": center,
            "sigma": sigma,
            "profile_offset": offset,
        },
        uncertainties={
            "amplitude": amplitude_error,
            "center": center_error,
            "sigma": sigma_error,
            "profile_offset": offset_error,
        },
        fwhm=float(FWHM_FACTOR * sigma),
        fwhm_error=float(FWHM_FACTOR * sigma_error),
        r_squared=_r_squared(profile, fitted_at_data),
    )


def analyze_knife_edge(
    x: np.ndarray, signal: np.ndarray, method: Method = "erf"
) -> KnifeEdgeResult:
    """Analyze arrays with either supported knife-edge fitting method."""

    if method == "erf":
        return fit_erf(x, signal)
    if method == "derivative":
        return fit_derivative(x, signal)
    raise KnifeEdgeError(f"Unsupported fitting method: {method}")

