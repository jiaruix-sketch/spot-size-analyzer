# Knife-Edge Spot Size Analyzer

This desktop application is based on `knife_edge.ipynb`. It supports direct experimental data entry, external data import, beam FWHM fitting, and separate export of the table, complete analysis results, and fit figures.

## Launch

Run this command from the project directory:

```powershell
python knife_edge_app.py
```

You can also double-click `Launch Knife-Edge Tool.bat`. If any dependencies are missing, install them with:

```powershell
python -m pip install -r requirements.txt
```

To create normal application shortcuts on the Windows Desktop and Start menu, run:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_shortcuts.ps1
```

The shortcut uses the system `wscript.exe` launcher and then starts the application through the current user's `pythonw.exe`, so no terminal window remains open. It uses an existing system application icon and does not require a generated image asset.

To preview the application with built-in synthetic data:

```powershell
python knife_edge_app.py --demo
```

## Workflow

1. Single-click a table cell and type. Enter moves down one row and Tab moves between columns. You can also copy two columns from Excel and press Ctrl+V in the table.
2. Drag across consecutive rows to select values. A blue fill handle appears at the lower-right corner of the selection. Drag it upward or downward to extend the series. Two or more numeric source values produce an arithmetic sequence using their inferred step; a single number or text pattern is repeated. Delete/Backspace clears the active column, and Ctrl+Z restores the previous edit.
3. Alternatively, click **Import File** and select an `.xlsx`, `.xls`, `.csv`, `.txt`, or `.dat` file. The import preview shows a numbered **Row** column. Drag over the rows containing the scan data (or click the first row and Shift-click the last), then choose the position and signal columns. No row numbers need to be typed. Excel row labels match the original worksheet rows. Text import automatically detects common delimiters and the `Time of Day` header used by SigScan files.
4. Change **Coordinate name** to `X`, `Y`, `Z`, or any experimental coordinate. The table header, axes, and exported column names update automatically. The signal name is editable as well.
5. Select the fit method and units, then click **Run Fit** or press F5.
6. The application displays FWHM, center position, sigma, R², the raw-signal fit, the inferred Gaussian beam profile, and the residuals.
7. **Export Data** saves the current two-column table as CSV, tab-delimited TXT, or Excel. **Export Results** creates a complete Excel/JSON report or a CSV summary. **Export Figure** supports PNG, PDF, and SVG.

## Fit Methods

The default method fits the raw knife-edge signal directly:

```text
S(x) = offset + height/2 × [1 + erf((x - x0)/(sqrt(2) × sigma))]
```

This approach avoids numerically differentiating noisy data and is normally more stable. Parameters are fitted with `scipy.optimize.curve_fit`. Initial plateau levels are the medians of the two ends of the trace, the initial center is the half-height position, and the initial sigma is one tenth of the scan range. Both increasing and decreasing traces are supported.

The alternative method first calculates `|dS/dx|` with `numpy.gradient`, then fits a Gaussian with a constant background:

```text
G(x) = A × exp(-(x - x0)^2/(2 × sigma^2)) + background
```

Both methods report the intensity-profile full width at half maximum:

```text
FWHM = 2 × sqrt(2 × ln(2)) × |sigma| ≈ 2.35482 × |sigma|
```

Parameter uncertainties come from the fit covariance matrix. The FWHM uncertainty is propagated using the same constant factor. R² is evaluated against the fitted quantity: the raw signal for the Erf method and the derivative profile for the Gaussian method.

## Project Files

- `knife_edge_app.py`: Tkinter desktop interface, import, and export.
- `knife_edge_core.py`: reusable and testable numerical fitting core.
- `knife_edge.ipynb`: original analysis notebook, unchanged.
- `tests/test_knife_edge_core.py`: synthetic-data regression tests.
- `install_shortcuts.ps1`: creates Desktop and Start menu application shortcuts.
- `launch_knife_edge.vbs`: starts the application without displaying a terminal window.
