"""Pixel sizes derived from font metrics.

Tk font sizes are in points and grow with display scaling (HiDPI and fractional
scaling), while ttk Treeview row heights and column widths are raw pixels.
Derive these dimensions from the actual font instead of hard-coding them.
"""
from __future__ import annotations


def tree_row_height(linespace: int, padding: int = 10, minimum: int = 24) -> int:
    """Row height that fits one line of text plus vertical breathing room."""
    return max(minimum, int(linespace) + padding)


def column_width(text_width: int, padding: int = 28, minimum: int = 0) -> int:
    """Column width that fits the widest expected text plus cell padding."""
    return max(minimum, int(text_width) + padding)
