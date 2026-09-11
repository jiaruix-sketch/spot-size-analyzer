"""Interactive desktop application for knife-edge spot-size measurements."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import matplotlib

matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

from knife_edge_core import KnifeEdgeError, KnifeEdgeResult, analyze_knife_edge


APP_TITLE = "Knife-Edge Spot Size Analyzer"
ERF_METHOD_LABEL = "Direct raw-signal fit (Erf, recommended)"
DERIVATIVE_METHOD_LABEL = "Numerical derivative + Gaussian fit"
DEFAULT_ROWS = 30


def excel_column_name(index: int) -> str:
    """Convert a zero-based column index to an Excel-style column name."""

    name = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _read_text_table(path: Path) -> pd.DataFrame:
    """Read common delimited files, including the notebook's SigScan format."""

    lines: list[str] = []
    used_encoding = "utf-8-sig"
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            lines = path.read_text(encoding=encoding).splitlines()
            used_encoding = encoding
            break
        except UnicodeDecodeError:
            continue
    if not lines:
        raise ValueError("The file is empty or could not be decoded.")

    header_row = next(
        (index for index, line in enumerate(lines) if line.lstrip().startswith("Time of Day")),
        0,
    )
    try:
        frame = pd.read_csv(
            path,
            sep=None,
            engine="python",
            skiprows=header_row,
            encoding=used_encoding,
        )
    except Exception:
        frame = pd.read_csv(path, sep="\t", skiprows=header_row, encoding=used_encoding)
    if frame.shape[1] < 2:
        frame = pd.read_csv(path, sep="\t", skiprows=header_row, encoding=used_encoding)
    return frame


def _write_table_data(path: Path, frame: pd.DataFrame, metadata: pd.DataFrame) -> None:
    """Write recorded table data in the format selected by the user."""

    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        with pd.ExcelWriter(path) as writer:
            frame.to_excel(writer, sheet_name="Raw Data", index=False)
            metadata.to_excel(writer, sheet_name="Metadata", index=False)
    elif suffix == ".txt":
        frame.to_csv(path, sep="\t", index=False, encoding="utf-8")
    else:
        frame.to_csv(path, index=False, encoding="utf-8")


class EditableDataTable(ttk.Frame):
    """A two-column Treeview with spreadsheet-like in-place editing and paste."""

    def __init__(
        self,
        parent: tk.Misc,
        rows: int = DEFAULT_ROWS,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.on_change = on_change
        self.tree = ttk.Treeview(
            self,
            columns=("position", "signal"),
            show="headings",
            selectmode="extended",
            height=22,
        )
        self.tree.heading("position", text="Knife position x")
        self.tree.heading("signal", text="Signal")
        self.tree.column("position", width=145, anchor="center", stretch=True)
        self.tree.column("signal", width=145, anchor="center", stretch=True)

        y_scroll = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        x_scroll = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self._editor: ttk.Entry | None = None
        self._editing_item: str | None = None
        self._editing_column = 0
        self._active_column = 0
        self._selection_anchor: str | None = None
        self._press_item: str | None = None
        self._press_xy = (0, 0)
        self._dragging_selection = False
        self._fill_source: tuple[int, int] | None = None
        self._fill_target: int | None = None
        self._undo_stack: list[list[tuple[str, str]]] = []

        self._fill_handle = tk.Frame(
            self.tree,
            background="#0078d4",
            cursor="crosshair",
            width=8,
            height=8,
            borderwidth=1,
            relief="solid",
        )
        self._fill_handle.bind("<ButtonPress-1>", self._start_fill)
        self._fill_handle.bind("<B1-Motion>", self._drag_fill)
        self._fill_handle.bind("<ButtonRelease-1>", self._finish_fill)

        self.tree.bind("<ButtonPress-1>", self._cell_press)
        self.tree.bind("<B1-Motion>", self._cell_drag)
        self.tree.bind("<ButtonRelease-1>", self._cell_release)
        self.tree.bind("<KeyPress>", self._tree_keypress)
        self.tree.bind("<Delete>", self._clear_selected)
        self.tree.bind("<BackSpace>", self._clear_selected)
        self.tree.bind("<Control-c>", self._copy)
        self.tree.bind("<Control-C>", self._copy)
        self.tree.bind("<Control-v>", self._paste)
        self.tree.bind("<Control-V>", self._paste)
        self.tree.bind("<Control-z>", self._undo)
        self.tree.bind("<Control-Z>", self._undo)
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self.after_idle(self._place_fill_handle))
        self.tree.bind("<MouseWheel>", lambda _event: self.after_idle(self._place_fill_handle), add="+")
        self.clear(rows, notify=False)

    def set_headers(self, coordinate_name: str, signal_name: str) -> None:
        self.tree.heading("position", text=coordinate_name.strip() or "Position")
        self.tree.heading("signal", text=signal_name.strip() or "Signal")

    def _snapshot(self) -> list[tuple[str, str]]:
        snapshot: list[tuple[str, str]] = []
        for item in self.tree.get_children():
            values = list(self.tree.item(item, "values")) + ["", ""]
            snapshot.append((str(values[0]), str(values[1])))
        return snapshot

    def _push_undo(self) -> None:
        self._undo_stack.append(self._snapshot())
        del self._undo_stack[:-50]

    def add_row(self, values: tuple[object, object] = ("", "")) -> str:
        return self.tree.insert("", "end", values=values)

    def add_blank_row(self) -> None:
        item = self.add_row()
        self.tree.selection_set(item)
        self.tree.see(item)

    def clear(self, rows: int = DEFAULT_ROWS, *, notify: bool = True) -> None:
        self.finish_edit()
        self.tree.delete(*self.tree.get_children())
        for _ in range(rows):
            self.add_row()
        if notify:
            self._notify_change()

    def delete_selected_rows(self) -> None:
        self.finish_edit()
        selected = self.tree.selection()
        if selected:
            self.tree.delete(*selected)
        while len(self.tree.get_children()) < 5:
            self.add_row()
        if selected:
            self._notify_change()

    def set_data(self, x: np.ndarray, signal: np.ndarray) -> None:
        self.clear(0, notify=False)
        for x_value, signal_value in zip(x, signal):
            self.add_row((f"{float(x_value):.12g}", f"{float(signal_value):.12g}"))
        for _ in range(max(5, DEFAULT_ROWS - len(x))):
            self.add_row()
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0])
            self.tree.see(children[0])
        self._notify_change()

    def get_data(self) -> tuple[np.ndarray, np.ndarray]:
        self.finish_edit()
        positions: list[float] = []
        signals: list[float] = []
        partial_rows: list[int] = []
        invalid_rows: list[int] = []
        for row_number, item in enumerate(self.tree.get_children(), start=1):
            values = list(self.tree.item(item, "values"))
            values += [""] * (2 - len(values))
            position_text = str(values[0]).strip()
            signal_text = str(values[1]).strip()
            if not position_text and not signal_text:
                continue
            if not position_text or not signal_text:
                partial_rows.append(row_number)
                continue
            try:
                positions.append(float(position_text.replace(",", "")))
                signals.append(float(signal_text.replace(",", "")))
            except ValueError:
                invalid_rows.append(row_number)

        if partial_rows:
            joined = ", ".join(map(str, partial_rows[:8]))
            raise KnifeEdgeError(f"Row(s) {joined} contain only one value. Complete or clear them.")
        if invalid_rows:
            joined = ", ".join(map(str, invalid_rows[:8]))
            raise KnifeEdgeError(f"Row(s) {joined} contain non-numeric values.")
        if len(positions) < 5:
            raise KnifeEdgeError("At least five valid data pairs are required.")
        return np.asarray(positions), np.asarray(signals)

    def finish_edit(self) -> None:
        if self._editor is not None:
            self._commit_edit()

    def _cell_press(self, event: tk.Event) -> str | None:
        item = self.tree.identify_row(event.y)
        column_id = self.tree.identify_column(event.x)
        if not item or column_id not in ("#1", "#2"):
            return None
        self.finish_edit()
        self.tree.focus_set()
        self._active_column = int(column_id[1:]) - 1
        self._press_item = item
        self._press_xy = (event.x, event.y)
        self._dragging_selection = False

        if event.state & 0x0001 and self._selection_anchor is not None:
            self._select_item_range(self._selection_anchor, item)
        else:
            self._selection_anchor = item
            self.tree.selection_set(item)
        self.tree.focus(item)
        self._place_fill_handle()
        return "break"

    def _cell_drag(self, event: tk.Event) -> str:
        if self._press_item is None:
            return "break"
        if abs(event.x - self._press_xy[0]) + abs(event.y - self._press_xy[1]) < 5:
            return "break"
        self._dragging_selection = True
        if event.y > self.tree.winfo_height() - 16:
            self.tree.yview_scroll(1, "units")
            self.tree.update_idletasks()
        elif event.y < 24:
            self.tree.yview_scroll(-1, "units")
            self.tree.update_idletasks()
        item = self.tree.identify_row(event.y)
        if item:
            self._select_item_range(self._press_item, item)
            self.tree.see(item)
        self._fill_handle.place_forget()
        return "break"

    def _cell_release(self, event: tk.Event) -> str:
        item = self._press_item
        dragged = self._dragging_selection
        self._press_item = None
        self._dragging_selection = False
        if item and not dragged and not (event.state & 0x0001):
            self._open_cell(item, self._active_column)
        else:
            self._place_fill_handle()
        return "break"

    def _select_item_range(self, first: str, last: str) -> None:
        children = list(self.tree.get_children())
        if first not in children or last not in children:
            return
        first_index = children.index(first)
        last_index = children.index(last)
        low, high = sorted((first_index, last_index))
        self.tree.selection_set(children[low : high + 1])

    def _tree_keypress(self, event: tk.Event) -> str | None:
        if event.state & 0x0004:
            return None
        children = list(self.tree.get_children())
        if not children:
            return "break"
        selected = self.tree.selection()
        item = selected[-1] if selected else children[0]
        index = children.index(item)
        key = event.keysym
        if key == "F2":
            self._open_cell(item, self._active_column)
            return "break"
        if key in ("Return", "Down", "Up", "Left", "Right", "Tab", "ISO_Left_Tab"):
            row_delta = 0
            column_delta = 0
            if key in ("Return", "Down"):
                row_delta = 1
            elif key == "Up":
                row_delta = -1
            elif key in ("Right", "Tab"):
                column_delta = 1
            else:
                column_delta = -1
            new_column = self._active_column + column_delta
            if new_column > 1:
                new_column = 0
                row_delta += 1
            elif new_column < 0:
                new_column = 1
                row_delta -= 1
            new_index = max(0, index + row_delta)
            if new_index >= len(children):
                self.add_row()
                children = list(self.tree.get_children())
                new_index = len(children) - 1
            self._active_column = new_column
            new_item = children[new_index]
            self._selection_anchor = new_item
            self.tree.selection_set(new_item)
            self.tree.focus(new_item)
            self.tree.see(new_item)
            self.after_idle(self._place_fill_handle)
            return "break"
        if event.char and event.char.isprintable():
            self._open_cell(item, self._active_column, replace_text=event.char)
            return "break"
        return None

    def _place_fill_handle(self) -> None:
        if self._editor is not None:
            self._fill_handle.place_forget()
            return
        selected = self._selected_indices()
        if not selected:
            self._fill_handle.place_forget()
            return
        children = list(self.tree.get_children())
        item = children[max(selected)]
        box = self.tree.bbox(item, f"#{self._active_column + 1}")
        if not box:
            self._fill_handle.place_forget()
            return
        size = 8
        self._fill_handle.place(
            x=box[0] + box[2] - size // 2,
            y=box[1] + box[3] - size // 2,
            width=size,
            height=size,
        )
        self._fill_handle.lift()

    def _selected_indices(self) -> list[int]:
        children = list(self.tree.get_children())
        selected = set(self.tree.selection())
        return [index for index, item in enumerate(children) if item in selected]

    def _start_fill(self, _event: tk.Event) -> str:
        selected = self._selected_indices()
        if not selected:
            return "break"
        self.finish_edit()
        self._fill_source = (min(selected), max(selected))
        self._fill_target = max(selected)
        self._fill_handle.configure(cursor="sb_v_double_arrow")
        return "break"

    def _drag_fill(self, _event: tk.Event) -> str:
        if self._fill_source is None:
            return "break"
        pointer_y = self.tree.winfo_pointery() - self.tree.winfo_rooty()
        if pointer_y > self.tree.winfo_height() - 16:
            self.tree.yview_scroll(1, "units")
            self.tree.update_idletasks()
        elif pointer_y < 24:
            self.tree.yview_scroll(-1, "units")
            self.tree.update_idletasks()
        item = self.tree.identify_row(pointer_y)
        children = list(self.tree.get_children())
        if item in children:
            target = children.index(item)
        elif pointer_y < 0:
            target = 0
        else:
            target = len(children) - 1
        self._fill_target = target
        source_start, source_end = self._fill_source
        low = min(source_start, target)
        high = max(source_end, target)
        self.tree.selection_set(children[low : high + 1])
        return "break"

    def _finish_fill(self, _event: tk.Event) -> str:
        source = self._fill_source
        target = self._fill_target
        self._fill_source = None
        self._fill_target = None
        self._fill_handle.configure(cursor="crosshair")
        if source is None or target is None:
            self._place_fill_handle()
            return "break"
        source_start, source_end = source
        if source_start <= target <= source_end:
            self._place_fill_handle()
            return "break"

        children = list(self.tree.get_children())
        column = self._active_column
        source_values = []
        for index in range(source_start, source_end + 1):
            values = list(self.tree.item(children[index], "values")) + ["", ""]
            source_values.append(str(values[column]))
        self._push_undo()

        numeric_values: list[float] | None
        try:
            numeric_values = [float(value.replace(",", "")) for value in source_values]
        except ValueError:
            numeric_values = None
        if numeric_values is not None and len(numeric_values) >= 2:
            step = (numeric_values[-1] - numeric_values[0]) / (len(numeric_values) - 1)
        else:
            step = 0.0

        fill_indices = (
            range(source_end + 1, target + 1)
            if target > source_end
            else range(source_start - 1, target - 1, -1)
        )
        for index in fill_indices:
            if numeric_values is not None:
                value = numeric_values[0] + step * (index - source_start)
                rendered = f"{value:.12g}"
            else:
                rendered = source_values[(index - source_start) % len(source_values)]
            values = list(self.tree.item(children[index], "values")) + ["", ""]
            values[column] = rendered
            self.tree.item(children[index], values=values[:2])

        low = min(source_start, target)
        high = max(source_end, target)
        self.tree.selection_set(children[low : high + 1])
        self._selection_anchor = children[low]
        self._notify_change()
        self.after_idle(self._place_fill_handle)
        return "break"

    def _begin_edit(self, event: tk.Event) -> str | None:
        item = self.tree.identify_row(event.y)
        column_id = self.tree.identify_column(event.x)
        if not item or column_id not in ("#1", "#2"):
            return None
        column_index = int(column_id[1:]) - 1
        return self._start_editor(item, column_index)

    def _start_editor(self, item: str, column_index: int) -> str | None:
        if self._editor is not None:
            self._commit_edit()
        box = self.tree.bbox(item, f"#{column_index + 1}")
        if not box:
            return None

        values = list(self.tree.item(item, "values"))
        values += [""] * (2 - len(values))
        self._editing_item = item
        self._editing_column = column_index
        self._active_column = column_index
        self._fill_handle.place_forget()
        self._editor = ttk.Entry(self.tree)
        self._editor.insert(0, values[column_index])
        self._editor.select_range(0, "end")
        self._editor.place(x=box[0], y=box[1], width=box[2], height=box[3])
        self._editor.focus_set()
        self._editor.bind("<Return>", self._commit_and_move)
        self._editor.bind("<Tab>", self._commit_and_tab)
        self._editor.bind("<Down>", self._commit_and_move)
        self._editor.bind("<Up>", self._commit_and_move_up)
        self._editor.bind("<Escape>", self._cancel_edit)
        self._editor.bind("<FocusOut>", self._commit_edit)
        return "break"

    def _commit_edit(self, _event: tk.Event | None = None) -> None:
        if self._editor is None or self._editing_item is None:
            return
        editor = self._editor
        value = editor.get().strip()
        values = list(self.tree.item(self._editing_item, "values"))
        values += [""] * (2 - len(values))
        old_value = str(values[self._editing_column])
        values[self._editing_column] = value
        if value != old_value:
            self._push_undo()
        self.tree.item(self._editing_item, values=values[:2])
        self._editor = None
        editor.destroy()
        if value != old_value:
            self._notify_change()
        self.after_idle(self._place_fill_handle)

    def _commit_and_move(self, _event: tk.Event) -> str:
        item = self._editing_item
        column = self._editing_column
        self._commit_edit()
        children = list(self.tree.get_children())
        if item in children:
            index = children.index(item) + 1
            if index >= len(children):
                self.add_row()
                children = list(self.tree.get_children())
            self._open_cell(children[index], column)
        return "break"

    def _commit_and_move_up(self, _event: tk.Event) -> str:
        item = self._editing_item
        column = self._editing_column
        self._commit_edit()
        children = list(self.tree.get_children())
        if item in children:
            index = max(0, children.index(item) - 1)
            self._open_cell(children[index], column)
        return "break"

    def _commit_and_tab(self, _event: tk.Event) -> str:
        item = self._editing_item
        column = self._editing_column
        self._commit_edit()
        if item is None:
            return "break"
        if column == 0:
            self._open_cell(item, 1)
        else:
            children = list(self.tree.get_children())
            index = children.index(item) + 1
            if index >= len(children):
                self.add_row()
                children = list(self.tree.get_children())
            self._open_cell(children[index], 0)
        return "break"

    def _open_cell(self, item: str, column: int, replace_text: str | None = None) -> None:
        self.tree.selection_set(item)
        self._selection_anchor = item
        self._active_column = column
        self.tree.see(item)
        self.tree.update_idletasks()
        box = self.tree.bbox(item, f"#{column + 1}")
        if not box:
            return
        self._start_editor(item, column)
        if replace_text is not None and self._editor is not None:
            self._editor.delete(0, "end")
            self._editor.insert(0, replace_text)
            self._editor.icursor("end")

    def _cancel_edit(self, _event: tk.Event | None = None) -> str:
        if self._editor is not None:
            self._editor.destroy()
            self._editor = None
        self.after_idle(self._place_fill_handle)
        return "break"

    def _clear_selected(self, _event: tk.Event | None = None) -> str:
        self.finish_edit()
        selected = self.tree.selection()
        changed = False
        if selected:
            self._push_undo()
        for item in selected:
            values = list(self.tree.item(item, "values")) + ["", ""]
            if str(values[self._active_column]):
                changed = True
            values[self._active_column] = ""
            self.tree.item(item, values=values[:2])
        if changed:
            self._notify_change()
        self.after_idle(self._place_fill_handle)
        return "break"

    def _copy(self, _event: tk.Event | None = None) -> str:
        self.finish_edit()
        selected = self._selected_indices()
        if not selected:
            return "break"
        children = list(self.tree.get_children())
        copied: list[str] = []
        for index in selected:
            values = list(self.tree.item(children[index], "values")) + ["", ""]
            copied.append(str(values[self._active_column]))
        self.clipboard_clear()
        self.clipboard_append("\n".join(copied))
        return "break"

    def _paste(self, _event: tk.Event | None = None) -> str:
        try:
            clipboard = self.clipboard_get()
        except tk.TclError:
            return "break"
        pasted_rows = [line.split("\t") for line in clipboard.strip().splitlines()]
        if not pasted_rows:
            return "break"
        children = list(self.tree.get_children())
        selected = self.tree.selection()
        start = children.index(selected[0]) if selected and selected[0] in children else 0
        start_column = self._active_column
        while len(children) < start + len(pasted_rows):
            self.add_row()
            children = list(self.tree.get_children())
        self._push_undo()
        max_column = start_column
        for offset, row in enumerate(pasted_rows):
            old = list(self.tree.item(children[start + offset], "values"))
            old += [""] * (2 - len(old))
            for column_offset, value in enumerate(row):
                column = start_column + column_offset
                if column > 1:
                    break
                old[column] = value.strip()
                max_column = max(max_column, column)
            self.tree.item(children[start + offset], values=old[:2])
        end = start + len(pasted_rows) - 1
        self.tree.selection_set(children[start : end + 1])
        self._selection_anchor = children[start]
        self._active_column = min(max_column, 1)
        self._notify_change()
        self.after_idle(self._place_fill_handle)
        return "break"

    def _undo(self, _event: tk.Event | None = None) -> str:
        self.finish_edit()
        if not self._undo_stack:
            return "break"
        snapshot = self._undo_stack.pop()
        self.tree.delete(*self.tree.get_children())
        for values in snapshot:
            self.add_row(values)
        self._notify_change()
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0])
            self._selection_anchor = children[0]
        self.after_idle(self._place_fill_handle)
        return "break"

    def _notify_change(self) -> None:
        if self.on_change is not None:
            self.on_change()


class ImportDialog(tk.Toplevel):
    """Column/range mapping dialog for an external Excel or text file."""

    def __init__(self, parent: tk.Misc, path: Path) -> None:
        super().__init__(parent)
        self.parent = parent
        self.path = path
        self.result: tuple[np.ndarray, np.ndarray, str, str] | None = None
        self.frame: pd.DataFrame | None = None
        self._preview_anchor: str | None = None
        self.title(f"Import Data — {path.name}")
        self.geometry("780x580")
        self.minsize(680, 500)
        self.transient(parent)
        self.grab_set()

        self.sheet_var = tk.StringVar()
        self.x_column_var = tk.StringVar()
        self.signal_column_var = tk.StringVar()
        self.sign_var = tk.StringVar(value="1")
        self.info_var = tk.StringVar(value="Reading file...")
        self.selected_rows_var = tk.StringVar(value="No rows selected")

        options = ttk.LabelFrame(self, text="Import Settings", padding=10)
        options.pack(fill="x", padx=12, pady=(12, 6))
        self.sheet_label = ttk.Label(options, text="Worksheet")
        self.sheet_combo = ttk.Combobox(options, textvariable=self.sheet_var, state="readonly", width=18)
        ttk.Label(options, text="Selected rows").grid(row=0, column=2, padx=(18, 4), pady=4)
        ttk.Label(options, textvariable=self.selected_rows_var).grid(
            row=0, column=3, columnspan=3, sticky="w", pady=4
        )
        self.sheet_label.grid(row=0, column=0, padx=(0, 4), pady=4)
        self.sheet_combo.grid(row=0, column=1, pady=4)

        ttk.Label(options, text="Position column x").grid(row=1, column=0, padx=(0, 4), pady=4)
        self.x_combo = ttk.Combobox(options, textvariable=self.x_column_var, state="readonly", width=18)
        self.x_combo.grid(row=1, column=1, pady=4)
        ttk.Label(options, text="Signal column").grid(row=1, column=2, padx=(18, 4), pady=4)
        self.signal_combo = ttk.Combobox(options, textvariable=self.signal_column_var, state="readonly", width=18)
        self.signal_combo.grid(row=1, column=3, pady=4)
        ttk.Label(options, text="Signal multiplier").grid(row=1, column=4, padx=(12, 4), pady=4)
        ttk.Combobox(options, textvariable=self.sign_var, values=("1", "-1"), state="readonly", width=6).grid(
            row=1, column=5, pady=4
        )

        ttk.Label(self, textvariable=self.info_var).pack(anchor="w", padx=14, pady=(2, 4))
        preview_frame = ttk.Frame(self)
        preview_frame.pack(fill="both", expand=True, padx=12, pady=4)
        self.preview = ttk.Treeview(
            preview_frame,
            show="tree headings",
            selectmode="extended",
            height=15,
        )
        self.preview.heading("#0", text="Row")
        self.preview.column("#0", width=64, minwidth=52, anchor="center", stretch=False)
        preview_y = ttk.Scrollbar(preview_frame, orient="vertical", command=self.preview.yview)
        preview_x = ttk.Scrollbar(preview_frame, orient="horizontal", command=self.preview.xview)
        self.preview.configure(yscrollcommand=preview_y.set, xscrollcommand=preview_x.set)
        self.preview.grid(row=0, column=0, sticky="nsew")
        preview_y.grid(row=0, column=1, sticky="ns")
        preview_x.grid(row=1, column=0, sticky="ew")
        preview_frame.rowconfigure(0, weight=1)
        preview_frame.columnconfigure(0, weight=1)

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", padx=12, pady=(4, 12))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Import", command=self._accept).pack(side="right", padx=8)

        self.sheet_combo.bind("<<ComboboxSelected>>", lambda _event: self._load_sheet())
        self.preview.bind("<ButtonPress-1>", self._preview_press)
        self.preview.bind("<B1-Motion>", self._preview_drag)
        self.preview.bind("<ButtonRelease-1>", self._preview_release)
        self.preview.bind("<<TreeviewSelect>>", self._update_selection_summary)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._initialize_source()

    def show(self) -> tuple[np.ndarray, np.ndarray, str, str] | None:
        self.wait_window()
        return self.result

    def _initialize_source(self) -> None:
        suffix = self.path.suffix.lower()
        try:
            if suffix in {".xlsx", ".xls"}:
                with pd.ExcelFile(self.path) as workbook:
                    sheet_names = list(workbook.sheet_names)
                self.sheet_combo.configure(values=sheet_names)
                self.sheet_var.set(sheet_names[0])
                self._load_sheet()
            else:
                self.sheet_label.grid_remove()
                self.sheet_combo.grid_remove()
                self.frame = _read_text_table(self.path)
                self._configure_frame(excel_mode=False)
        except Exception as exc:
            messagebox.showerror("Import Failed", str(exc), parent=self)
            self.destroy()

    def _load_sheet(self) -> None:
        try:
            self.frame = pd.read_excel(
                self.path,
                sheet_name=self.sheet_var.get(),
                header=None,
                dtype=object,
            )
            self.frame.columns = [excel_column_name(index) for index in range(self.frame.shape[1])]
            self._configure_frame(excel_mode=True)
        except Exception as exc:
            messagebox.showerror("Worksheet Read Failed", str(exc), parent=self)

    def _configure_frame(self, *, excel_mode: bool) -> None:
        assert self.frame is not None
        columns = [str(column) for column in self.frame.columns]
        self.x_combo.configure(values=columns)
        self.signal_combo.configure(values=columns)
        if columns:
            self.x_column_var.set(columns[0])
            self.signal_column_var.set(columns[min(1, len(columns) - 1)])
        row_description = "original Excel rows" if excel_mode else "parsed data rows"
        self.info_var.set(
            f"{len(self.frame)} rows and {len(columns)} columns. Drag over the numbered preview rows "
            f"to select the {row_description} to import. Shift-click also extends the selection."
        )
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        assert self.frame is not None
        self.preview.delete(*self.preview.get_children())
        columns = [str(column) for column in self.frame.columns]
        self.preview.configure(columns=columns)
        for column in columns:
            self.preview.heading(column, text=column)
            self.preview.column(column, width=120, anchor="center", stretch=False)
        for position, (_index, row) in enumerate(self.frame.iterrows(), start=1):
            values = ["" if pd.isna(value) else str(value) for value in row]
            self.preview.insert("", "end", iid=f"preview_row_{position}", text=str(position), values=values)
        self._preview_anchor = None
        self.selected_rows_var.set("No rows selected")

    def _preview_press(self, event: tk.Event) -> str | None:
        if self.preview.identify_region(event.x, event.y) not in {"tree", "cell"}:
            return None
        item = self.preview.identify_row(event.y)
        if not item:
            return None
        if event.state & 0x0001 and self._preview_anchor is not None:
            self._select_preview_range(self._preview_anchor, item)
        else:
            self._preview_anchor = item
            self.preview.selection_set(item)
        self.preview.focus(item)
        self._update_selection_summary()
        return "break"

    def _preview_drag(self, event: tk.Event) -> str:
        if self._preview_anchor is None:
            return "break"
        if event.y > self.preview.winfo_height() - 16:
            self.preview.yview_scroll(1, "units")
            self.preview.update_idletasks()
        elif event.y < 24:
            self.preview.yview_scroll(-1, "units")
            self.preview.update_idletasks()
        item = self.preview.identify_row(event.y)
        if item:
            self._select_preview_range(self._preview_anchor, item)
            self.preview.see(item)
            self._update_selection_summary()
        return "break"

    def _preview_release(self, _event: tk.Event) -> str:
        self._update_selection_summary()
        return "break"

    def _select_preview_range(self, first: str, last: str) -> None:
        children = list(self.preview.get_children())
        if first not in children or last not in children:
            return
        first_index = children.index(first)
        last_index = children.index(last)
        low, high = sorted((first_index, last_index))
        self.preview.selection_set(children[low : high + 1])

    def _selected_preview_indices(self) -> list[int]:
        children = list(self.preview.get_children())
        selected = set(self.preview.selection())
        return [index for index, item in enumerate(children) if item in selected]

    def _update_selection_summary(self, _event: tk.Event | None = None) -> None:
        indices = self._selected_preview_indices()
        if not indices:
            self.selected_rows_var.set("No rows selected")
            return
        first = indices[0] + 1
        last = indices[-1] + 1
        contiguous = len(indices) == last - first + 1
        if contiguous:
            self.selected_rows_var.set(f"{first}–{last} ({len(indices)} rows)")
        else:
            self.selected_rows_var.set(f"{len(indices)} rows selected ({first}–{last}, non-contiguous)")

    def _accept(self) -> None:
        assert self.frame is not None
        try:
            selected_indices = self._selected_preview_indices()
            if not selected_indices:
                raise ValueError("Select the data rows directly in the preview before importing.")
            x_column = self.x_column_var.get()
            signal_column = self.signal_column_var.get()
            if not x_column or not signal_column or x_column == signal_column:
                raise ValueError("Choose two different data columns.")
            selected = self.frame.iloc[selected_indices]
            x = pd.to_numeric(selected[x_column], errors="coerce").to_numpy(dtype=float)
            signal = pd.to_numeric(selected[signal_column], errors="coerce").to_numpy(dtype=float)
            valid = np.isfinite(x) & np.isfinite(signal)
            dropped = int(valid.size - np.count_nonzero(valid))
            x = x[valid]
            signal = float(self.sign_var.get()) * signal[valid]
            if x.size < 5:
                raise ValueError(
                    "The selected rows contain fewer than five valid pairs. Check the selected rows and columns."
                )
            if dropped:
                proceed = messagebox.askyesno(
                    "Skip Invalid Rows",
                    f"{dropped} row(s) are blank or non-numeric and will be skipped. Continue?",
                    parent=self,
                )
                if not proceed:
                    return
            self.result = (x, signal, x_column, signal_column)
            self.destroy()
        except Exception as exc:
            messagebox.showerror("Invalid Import Settings", str(exc), parent=self)


class KnifeEdgeApp:
    """Main interactive knife-edge analysis window."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1280x800")
        self.root.minsize(1030, 680)
        self.result: KnifeEdgeResult | None = None
        self.source_name = "Manual entry"

        self.method_var = tk.StringVar(value=ERF_METHOD_LABEL)
        self.coordinate_name_var = tk.StringVar(value="X")
        self.signal_name_var = tk.StringVar(value="Signal")
        self.x_unit_var = tk.StringVar(value="mm")
        self.signal_unit_var = tk.StringVar(value="a.u.")
        self.status_var = tk.StringVar(
            value="Double-click the table to enter data, or import an Excel/TXT/CSV file."
        )
        self.result_text_var = tk.StringVar(value="Not analyzed")

        self._configure_style()
        self._build_menu()
        self._build_layout()
        self._bind_shortcuts()
        self._draw_empty_plot()

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        available = style.theme_names()
        if "vista" in available:
            style.theme_use("vista")
        elif "clam" in available:
            style.theme_use("clam")
        style.configure("Title.TLabel", font=("Segoe UI", 15, "bold"))
        style.configure("Result.TLabel", font=("Consolas", 10))
        matplotlib.rcParams["font.sans-serif"] = ["Segoe UI", "DejaVu Sans"]
        matplotlib.rcParams["axes.unicode_minus"] = False

    def _build_menu(self) -> None:
        menu = tk.Menu(self.root)
        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(label="New / Clear", command=self.new_table, accelerator="Ctrl+N")
        file_menu.add_command(label="Import File...", command=self.import_file, accelerator="Ctrl+O")
        file_menu.add_separator()
        file_menu.add_command(label="Export Table Data...", command=self.export_data, accelerator="Ctrl+S")
        file_menu.add_command(label="Export Analysis Results...", command=self.export_results)
        file_menu.add_command(label="Export Fit Figure...", command=self.export_plot)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.destroy)
        menu.add_cascade(label="File", menu=file_menu)

        analysis_menu = tk.Menu(menu, tearoff=False)
        analysis_menu.add_command(label="Run Fit", command=self.run_analysis, accelerator="F5")
        menu.add_cascade(label="Analysis", menu=analysis_menu)
        self.root.configure(menu=menu)

    def _build_layout(self) -> None:
        header = ttk.Frame(self.root, padding=(12, 10, 12, 6))
        header.pack(fill="x")
        ttk.Label(header, text=APP_TITLE, style="Title.TLabel").pack(side="left")
        ttk.Button(header, text="Import File", command=self.import_file).pack(side="right")
        ttk.Button(header, text="Run Fit  F5", command=self.run_analysis).pack(side="right", padx=8)

        paned = ttk.Panedwindow(self.root, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=12, pady=6)

        left = ttk.Frame(paned, padding=(0, 0, 8, 0))
        right = ttk.Frame(paned)
        paned.add(left, weight=1)
        paned.add(right, weight=3)

        settings = ttk.LabelFrame(left, text="Analysis Settings", padding=8)
        settings.pack(fill="x", pady=(0, 8))
        ttk.Label(settings, text="Fit method").grid(row=0, column=0, sticky="w", pady=3)
        method_combo = ttk.Combobox(
            settings,
            textvariable=self.method_var,
            values=(ERF_METHOD_LABEL, DERIVATIVE_METHOD_LABEL),
            state="readonly",
            width=30,
        )
        method_combo.grid(row=0, column=1, columnspan=3, sticky="ew", pady=3)
        method_combo.bind("<<ComboboxSelected>>", lambda _event: self._data_changed())
        ttk.Label(settings, text="Coordinate name").grid(row=1, column=0, sticky="w", pady=3)
        coordinate_entry = ttk.Entry(settings, textvariable=self.coordinate_name_var, width=10)
        coordinate_entry.grid(row=1, column=1, sticky="ew", pady=3)
        ttk.Label(settings, text="Signal name").grid(row=1, column=2, sticky="w", padx=(10, 0), pady=3)
        signal_entry = ttk.Entry(settings, textvariable=self.signal_name_var, width=10)
        signal_entry.grid(row=1, column=3, sticky="ew", pady=3)
        ttk.Label(settings, text="Position unit").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(settings, textvariable=self.x_unit_var, width=10).grid(row=2, column=1, sticky="ew", pady=3)
        ttk.Label(settings, text="Signal unit").grid(row=2, column=2, sticky="w", padx=(10, 0), pady=3)
        ttk.Entry(settings, textvariable=self.signal_unit_var, width=10).grid(row=2, column=3, sticky="ew", pady=3)
        settings.columnconfigure(1, weight=1)
        settings.columnconfigure(3, weight=1)

        table_header = ttk.Frame(left)
        table_header.pack(fill="x", pady=(0, 4))
        ttk.Label(
            table_header,
            text="Single-click to edit • Enter moves down\nDrag-select values, then drag the blue fill handle",
            justify="left",
        ).pack(side="left")
        ttk.Button(table_header, text="+ Row", width=7, command=lambda: self.table.add_blank_row()).pack(side="right")
        ttk.Button(table_header, text="Delete", width=7, command=self._delete_rows).pack(side="right", padx=4)

        self.table = EditableDataTable(left, on_change=self._data_changed)
        self.table.pack(fill="both", expand=True)
        coordinate_entry.bind("<KeyRelease>", lambda _event: self._labels_changed())
        signal_entry.bind("<KeyRelease>", lambda _event: self._labels_changed())
        self.table.set_headers(self.coordinate_name_var.get(), self.signal_name_var.get())

        result_box = ttk.LabelFrame(left, text="Fit Results", padding=8)
        result_box.pack(fill="x", pady=(8, 0))
        ttk.Label(
            result_box,
            textvariable=self.result_text_var,
            style="Result.TLabel",
            justify="left",
        ).pack(anchor="w")

        export_bar = ttk.Frame(left)
        export_bar.pack(fill="x", pady=(8, 0))
        ttk.Button(export_bar, text="Export Data", command=self.export_data).pack(side="left", expand=True, fill="x")
        ttk.Button(export_bar, text="Export Results", command=self.export_results).pack(
            side="left", expand=True, fill="x", padx=5
        )
        ttk.Button(export_bar, text="Export Figure", command=self.export_plot).pack(side="left", expand=True, fill="x")

        self.figure = Figure(figsize=(9, 7), dpi=100, constrained_layout=True)
        grid = self.figure.add_gridspec(2, 2, height_ratios=(1.15, 1.0))
        self.raw_axis = self.figure.add_subplot(grid[0, :])
        self.profile_axis = self.figure.add_subplot(grid[1, 0])
        self.residual_axis = self.figure.add_subplot(grid[1, 1])
        self.canvas = FigureCanvasTkAgg(self.figure, master=right)
        toolbar = NavigationToolbar2Tk(self.canvas, right, pack_toolbar=False)
        toolbar.update()
        toolbar.pack(fill="x")
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        status = ttk.Label(self.root, textvariable=self.status_var, relief="sunken", anchor="w", padding=(8, 3))
        status.pack(fill="x", side="bottom")

    def _bind_shortcuts(self) -> None:
        self.root.bind("<Control-n>", lambda _event: self.new_table())
        self.root.bind("<Control-o>", lambda _event: self.import_file())
        self.root.bind("<Control-s>", lambda _event: self.export_data())
        self.root.bind("<F5>", lambda _event: self.run_analysis())

    def new_table(self) -> None:
        if self._table_has_values() and not messagebox.askyesno(
            "Clear Table", "Clear the current table and analysis result?", parent=self.root
        ):
            return
        self.table.clear()
        self.result = None
        self.source_name = "Manual entry"
        self.result_text_var.set("Not analyzed")
        self.status_var.set("Table cleared. Ready for a new measurement.")
        self._draw_empty_plot()

    def _table_has_values(self) -> bool:
        for item in self.table.tree.get_children():
            if any(str(value).strip() for value in self.table.tree.item(item, "values")):
                return True
        return False

    def _data_changed(self) -> None:
        """Invalidate a fitted result as soon as its input or method changes."""

        if self.result is not None:
            self.result = None
            self.result_text_var.set("Data or fit method changed; run the fit again")
            self.status_var.set("The previous result is no longer current. Press F5 to refit.")
            self._draw_empty_plot()

    def _labels_changed(self) -> None:
        """Refresh table and plot labels without invalidating numerical results."""

        self.table.set_headers(self.coordinate_name_var.get(), self.signal_name_var.get())
        if self.result is not None:
            self._show_result(self.result)
            self._plot_result(self.result)

    def _column_names(self) -> tuple[str, str]:
        coordinate = self.coordinate_name_var.get().strip() or "Position"
        signal = self.signal_name_var.get().strip() or "Signal"
        if signal == coordinate:
            signal = f"{signal} signal"
        return coordinate, signal

    def _delete_rows(self) -> None:
        if not self.table.tree.selection():
            self.status_var.set("Select one or more rows to delete.")
            return
        self.table.delete_selected_rows()
        self.result = None
        self.result_text_var.set("Data changed; run the fit again")

    def import_file(self) -> None:
        filename = filedialog.askopenfilename(
            parent=self.root,
            title="Import Knife-Edge Data",
            filetypes=(
                ("Supported data files", "*.xlsx *.xls *.csv *.txt *.dat"),
                ("Excel", "*.xlsx *.xls"),
                ("Text data", "*.csv *.txt *.dat"),
                ("All files", "*.*"),
            ),
        )
        if not filename:
            return
        dialog = ImportDialog(self.root, Path(filename))
        imported = dialog.show()
        if imported is None:
            return
        x, signal, coordinate_name, signal_name = imported
        self.coordinate_name_var.set(coordinate_name)
        self.signal_name_var.set(signal_name)
        self.table.set_headers(coordinate_name, signal_name)
        self.table.set_data(x, signal)
        self.source_name = Path(filename).name
        self.result = None
        self.result_text_var.set("Imported; waiting for analysis")
        self.status_var.set(f"Imported {len(x)} data pairs from {self.source_name}. Press F5 to fit.")
        self._plot_unfitted(x, signal)

    def run_analysis(self) -> None:
        try:
            x, signal = self.table.get_data()
            method = "erf" if self.method_var.get() == ERF_METHOD_LABEL else "derivative"
            self.status_var.set("Fitting...")
            self.root.update_idletasks()
            self.result = analyze_knife_edge(x, signal, method=method)
            self._show_result(self.result)
            self._plot_result(self.result)
            self.status_var.set(
                f"Fit complete: FWHM = {self.result.fwhm:.6g} {self.x_unit_var.get().strip()}, "
                f"R² = {self.result.r_squared:.5f}"
            )
        except (KnifeEdgeError, ValueError) as exc:
            self.result = None
            self.status_var.set("Fit failed. Check the input data.")
            messagebox.showerror("Fit Failed", str(exc), parent=self.root)
        except Exception as exc:
            self.result = None
            self.status_var.set("An unexpected fit error occurred.")
            messagebox.showerror("Fit Error", f"{type(exc).__name__}: {exc}", parent=self.root)

    def _show_result(self, result: KnifeEdgeResult) -> None:
        unit = self.x_unit_var.get().strip()
        center_error = result.uncertainties.get("center", float("nan"))
        sigma_error = result.uncertainties.get("sigma", float("nan"))
        method_label = "Raw-signal Erf fit" if result.method == "erf" else "Derivative Gaussian fit"
        self.result_text_var.set(
            f"Method  {method_label}\n"
            f"FWHM   {self._plus_minus(result.fwhm, result.fwhm_error)} {unit}\n"
            f"Center {self._plus_minus(result.center, center_error)} {unit}\n"
            f"Sigma  {self._plus_minus(result.sigma, sigma_error)} {unit}\n"
            f"R²     {result.r_squared:.6f}"
        )

    @staticmethod
    def _plus_minus(value: float, error: float) -> str:
        if math.isfinite(error):
            return f"{value:.7g} ± {error:.2g}"
        return f"{value:.7g}"

    def _draw_empty_plot(self) -> None:
        for axis in (self.raw_axis, self.profile_axis, self.residual_axis):
            axis.clear()
            axis.grid(alpha=0.2)
        self.raw_axis.set_title("Raw Knife-Edge Signal")
        self.raw_axis.text(
            0.5,
            0.5,
            "Enter or import data, then run the fit",
            ha="center",
            va="center",
            transform=self.raw_axis.transAxes,
        )
        self.profile_axis.set_title("Inferred Beam Intensity Profile")
        self.residual_axis.set_title("Fit Residuals")
        self.canvas.draw_idle()

    def _plot_unfitted(self, x: np.ndarray, signal: np.ndarray) -> None:
        coordinate_name, signal_name = self._column_names()
        self._draw_empty_plot()
        self.raw_axis.clear()
        self.raw_axis.plot(x, signal, "o-", markersize=4, linewidth=1, label="Imported data")
        self.raw_axis.set_xlabel(self._axis_label(coordinate_name, self.x_unit_var.get()))
        self.raw_axis.set_ylabel(self._axis_label(signal_name, self.signal_unit_var.get()))
        self.raw_axis.set_title("Raw Knife-Edge Signal (Not Fitted)")
        self.raw_axis.grid(alpha=0.25)
        self.raw_axis.legend()
        self.canvas.draw_idle()

    @staticmethod
    def _axis_label(label: str, unit: str) -> str:
        unit = unit.strip()
        return f"{label} ({unit})" if unit else label

    def _plot_result(self, result: KnifeEdgeResult) -> None:
        x_unit = self.x_unit_var.get()
        signal_unit = self.signal_unit_var.get()
        coordinate_name, signal_name = self._column_names()
        for axis in (self.raw_axis, self.profile_axis, self.residual_axis):
            axis.clear()
            axis.grid(alpha=0.25)

        self.raw_axis.plot(result.x, result.signal, "o", markersize=4, label="Experimental data")
        if result.signal_fit is not None:
            self.raw_axis.plot(result.fit_x, result.signal_fit, "-", linewidth=2, label="Erf fit")
        self.raw_axis.axvline(result.center, color="tab:red", linestyle="--", linewidth=1, label="Beam center")
        self.raw_axis.set_xlabel(self._axis_label(coordinate_name, x_unit))
        self.raw_axis.set_ylabel(self._axis_label(signal_name, signal_unit))
        self.raw_axis.set_title(f"Raw Signal | FWHM = {result.fwhm:.6g} {x_unit.strip()}")
        self.raw_axis.legend()

        self.profile_axis.plot(result.profile_x, result.profile, "o", markersize=3, alpha=0.55, label="|dSignal/dx|")
        self.profile_axis.plot(result.fit_x, result.profile_fit, "-", linewidth=2, label="Gaussian beam profile")
        self.profile_axis.axvline(result.center, color="tab:red", linestyle="--", linewidth=1)
        self.profile_axis.set_xlabel(self._axis_label(coordinate_name, x_unit))
        self.profile_axis.set_ylabel("Relative intensity")
        self.profile_axis.set_title("Beam Intensity Profile")
        self.profile_axis.legend(fontsize=8)

        self.residual_axis.axhline(0, color="black", linewidth=1)
        self.residual_axis.plot(result.residual_x, result.residual, "o", markersize=3, color="tab:purple")
        self.residual_axis.set_xlabel(self._axis_label(coordinate_name, x_unit))
        residual_label = "Signal − Erf" if result.method == "erf" else "Profile − Gaussian"
        self.residual_axis.set_ylabel(residual_label)
        self.residual_axis.set_title(f"Residuals | R² = {result.r_squared:.5f}")
        self.canvas.draw_idle()

    def export_data(self) -> None:
        try:
            x, signal = self.table.get_data()
        except KnifeEdgeError as exc:
            messagebox.showerror("Cannot Export", str(exc), parent=self.root)
            return
        filename = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export Table Data",
            defaultextension=".csv",
            filetypes=(("CSV", "*.csv"), ("Tab-delimited text", "*.txt"), ("Excel", "*.xlsx")),
            initialfile="knife_edge_data.csv",
        )
        if not filename:
            return
        try:
            coordinate_name, signal_name = self._column_names()
            frame = pd.DataFrame({coordinate_name: x, signal_name: signal})
            path = Path(filename)
            metadata = pd.DataFrame(
                {
                    "field": [
                        "coordinate_name",
                        "signal_name",
                        "position_unit",
                        "signal_unit",
                        "source",
                    ],
                    "value": [
                        coordinate_name,
                        signal_name,
                        self.x_unit_var.get(),
                        self.signal_unit_var.get(),
                        self.source_name,
                    ],
                }
            )
            _write_table_data(path, frame, metadata)
            self.status_var.set(f"Table data exported to {path.name}")
        except Exception as exc:
            messagebox.showerror("Export Failed", str(exc), parent=self.root)

    def export_results(self) -> None:
        if not self._require_result():
            return
        assert self.result is not None
        filename = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export Analysis Results",
            defaultextension=".xlsx",
            filetypes=(("Complete Excel report", "*.xlsx"), ("CSV result summary", "*.csv"), ("Complete JSON data", "*.json")),
            initialfile="knife_edge_analysis.xlsx",
        )
        if not filename:
            return
        path = Path(filename)
        try:
            result = self.result
            coordinate_name, signal_name = self._column_names()
            summary = pd.DataFrame(result.summary_rows(self.x_unit_var.get().strip()))
            raw = pd.DataFrame({coordinate_name: result.x, signal_name: result.signal})
            curve_data: dict[str, np.ndarray] = {
                coordinate_name: result.fit_x,
                "gaussian_profile": result.profile_fit,
            }
            if result.signal_fit is not None:
                curve_data["erf_signal_fit"] = result.signal_fit
            curves = pd.DataFrame(curve_data)
            measured_profile = pd.DataFrame(
                {coordinate_name: result.profile_x, "absolute_derivative": result.profile}
            )
            residuals = pd.DataFrame(
                {coordinate_name: result.residual_x, "residual": result.residual}
            )
            if path.suffix.lower() == ".xlsx":
                with pd.ExcelWriter(path) as writer:
                    summary.to_excel(writer, sheet_name="Results", index=False)
                    raw.to_excel(writer, sheet_name="Raw Data", index=False)
                    curves.to_excel(writer, sheet_name="Fit Curve", index=False)
                    measured_profile.to_excel(writer, sheet_name="Measured Profile", index=False)
                    residuals.to_excel(writer, sheet_name="Residuals", index=False)
            elif path.suffix.lower() == ".json":
                payload = {
                    "source": self.source_name,
                    "coordinate_name": coordinate_name,
                    "signal_name": signal_name,
                    "position_unit": self.x_unit_var.get(),
                    "signal_unit": self.signal_unit_var.get(),
                    "summary": result.summary_rows(self.x_unit_var.get().strip()),
                    "raw_data": raw.to_dict(orient="list"),
                    "fit_curve": curves.to_dict(orient="list"),
                    "measured_profile": measured_profile.to_dict(orient="list"),
                    "residuals": residuals.to_dict(orient="list"),
                }
                path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            else:
                summary.to_csv(path, index=False)
            self.status_var.set(f"Analysis results exported to {path.name}")
        except Exception as exc:
            messagebox.showerror("Export Failed", str(exc), parent=self.root)

    def export_plot(self) -> None:
        if not self._require_result():
            return
        filename = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export Fit Figure",
            defaultextension=".png",
            filetypes=(("PNG image", "*.png"), ("PDF", "*.pdf"), ("SVG vector image", "*.svg")),
            initialfile="knife_edge_fit.png",
        )
        if not filename:
            return
        try:
            self.figure.savefig(filename, dpi=300, bbox_inches="tight")
            self.status_var.set(f"Fit figure exported to {Path(filename).name}")
        except Exception as exc:
            messagebox.showerror("Export Failed", str(exc), parent=self.root)

    def _require_result(self) -> bool:
        if self.result is None:
            messagebox.showinfo("No Analysis Result", "Run the fit first by clicking 'Run Fit' or pressing F5.", parent=self.root)
            return False
        return True

    def load_demo_data(self) -> None:
        """Populate deterministic data for development and demonstrations."""

        from scipy.special import erf

        x = np.linspace(-1.0, 1.2, 61)
        signal = 0.15 + 2.8 * 0.5 * (1 + erf((x - 0.12) / (np.sqrt(2) * 0.18)))
        signal += np.random.default_rng(42).normal(0, 0.015, x.size)
        self.table.set_data(x, signal)
        self.source_name = "Built-in demo"
        self.status_var.set("Demo data loaded. Press F5 to run the fit.")
        self._plot_unfitted(x, signal)


def main() -> None:
    root = tk.Tk()
    app = KnifeEdgeApp(root)
    if "--demo" in sys.argv:
        app.load_demo_data()
    root.mainloop()


if __name__ == "__main__":
    main()
