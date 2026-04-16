"""
duplicheck.gui
==============
Tkinter-based graphical user interface for DupliCheck.

Layout overview
---------------
┌─────────────────────────────────────────────────────────────┐
│  Toolbar: [Add Directory]  [Remove]  [Clear]  [Start Scan]  │
│           [Cancel]                                          │
├─────────────────────────────────────────────────────────────┤
│  Directories to scan (listbox)                              │
├─────────────────────────────────────────────────────────────┤
│  Progress bar  + status label                               │
├─────────────────────────────────────────────────────────────┤
│  Results tree (groups / individual files)                   │
│    Columns: File Name | Size | Full Path | Modified         │
├─────────────────────────────────────────────────────────────┤
│  Action bar: [Select All Dupes] [Invert]                    │
│              [Delete Selected] [Export Report]              │
│  Summary label                                              │
└─────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import os
import tkinter as tk
import tkinter.filedialog as fd
import tkinter.messagebox as mb
import tkinter.ttk as ttk
from datetime import datetime
from typing import Dict, List, Optional

from duplicheck.scanner import DuplicateScanner, human_readable_size, wasted_space

# ── Colour palette (light theme) ──────────────────────────────────────────
_CLR_BG = "#f5f5f5"
_CLR_HEADER = "#3c3f41"
_CLR_ACCENT = "#4a90d9"
_CLR_DUPE_ODD = "#fff3cd"   # warm yellow for duplicate rows (odd)
_CLR_DUPE_EVEN = "#ffe082"  # slightly deeper yellow (even)
_CLR_WHITE = "#ffffff"
_CLR_BTN = "#4a90d9"
_CLR_BTN_FG = "#ffffff"
_CLR_DANGER = "#e53935"


class DupliCheckApp(tk.Tk):
    """Main application window."""

    def __init__(self) -> None:
        super().__init__()
        self.title("DupliCheck – Duplicate File Finder")
        self.geometry("1050x720")
        self.minsize(800, 550)
        self.configure(bg=_CLR_BG)

        # Internal state
        self._scanner: Optional[DuplicateScanner] = None
        self._groups: List[List[str]] = []
        # Map treeview item ID → file path (for leaf nodes)
        self._item_path: Dict[str, str] = {}
        # Map treeview item ID → group index (for group nodes)
        self._item_group: Dict[str, int] = {}

        self._build_styles()
        self._build_ui()

    # ------------------------------------------------------------------
    # Style / theme
    # ------------------------------------------------------------------

    def _build_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")

        style.configure("TFrame", background=_CLR_BG)
        style.configure("Header.TFrame", background=_CLR_HEADER)

        style.configure(
            "Accent.TButton",
            background=_CLR_BTN,
            foreground=_CLR_BTN_FG,
            padding=(8, 4),
            font=("Segoe UI", 9, "bold"),
        )
        style.map("Accent.TButton", background=[("active", "#357abd")])

        style.configure(
            "Danger.TButton",
            background=_CLR_DANGER,
            foreground=_CLR_BTN_FG,
            padding=(8, 4),
            font=("Segoe UI", 9, "bold"),
        )
        style.map("Danger.TButton", background=[("active", "#b71c1c")])

        style.configure(
            "TLabel",
            background=_CLR_BG,
            font=("Segoe UI", 9),
        )
        style.configure(
            "Title.TLabel",
            background=_CLR_HEADER,
            foreground=_CLR_WHITE,
            font=("Segoe UI", 14, "bold"),
            padding=(12, 8),
        )
        style.configure(
            "Summary.TLabel",
            background=_CLR_BG,
            font=("Segoe UI", 9, "italic"),
        )

        style.configure(
            "Treeview",
            background=_CLR_WHITE,
            fieldbackground=_CLR_WHITE,
            rowheight=22,
            font=("Segoe UI", 9),
        )
        style.configure(
            "Treeview.Heading",
            background=_CLR_HEADER,
            foreground=_CLR_WHITE,
            font=("Segoe UI", 9, "bold"),
        )
        style.map("Treeview", background=[("selected", _CLR_ACCENT)])

        style.configure(
            "Horizontal.TProgressbar",
            troughcolor="#ddd",
            background=_CLR_ACCENT,
            thickness=16,
        )

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # ── Header ──────────────────────────────────────────────────────
        header = ttk.Frame(self, style="Header.TFrame")
        header.pack(fill=tk.X)
        ttk.Label(
            header,
            text="🔍  DupliCheck",
            style="Title.TLabel",
        ).pack(side=tk.LEFT)

        # ── Main paned window ───────────────────────────────────────────
        paned = ttk.PanedWindow(self, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # ── Top panel: directory list + toolbar ─────────────────────────
        top_frame = ttk.Frame(paned)
        paned.add(top_frame, weight=1)

        dir_label = ttk.Label(top_frame, text="Directories to scan:")
        dir_label.pack(anchor=tk.W)

        dir_frame = ttk.Frame(top_frame)
        dir_frame.pack(fill=tk.BOTH, expand=True, pady=(2, 4))

        self._dir_listbox = tk.Listbox(
            dir_frame,
            selectmode=tk.EXTENDED,
            font=("Segoe UI", 9),
            bg=_CLR_WHITE,
            relief=tk.FLAT,
            bd=1,
            highlightthickness=1,
            highlightcolor=_CLR_ACCENT,
        )
        self._dir_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        dir_scroll = ttk.Scrollbar(dir_frame, command=self._dir_listbox.yview)
        dir_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._dir_listbox.configure(yscrollcommand=dir_scroll.set)

        # Toolbar buttons
        btn_frame = ttk.Frame(top_frame)
        btn_frame.pack(fill=tk.X)

        ttk.Button(
            btn_frame, text="➕ Add Directory", style="Accent.TButton",
            command=self._add_directory,
        ).pack(side=tk.LEFT, padx=(0, 4))

        ttk.Button(
            btn_frame, text="➖ Remove Selected",
            command=self._remove_directories,
        ).pack(side=tk.LEFT, padx=(0, 4))

        ttk.Button(
            btn_frame, text="🗑 Clear All",
            command=self._clear_directories,
        ).pack(side=tk.LEFT, padx=(0, 12))

        self._scan_btn = ttk.Button(
            btn_frame, text="▶  Start Scan", style="Accent.TButton",
            command=self._start_scan,
        )
        self._scan_btn.pack(side=tk.LEFT, padx=(0, 4))

        self._cancel_btn = ttk.Button(
            btn_frame, text="⏹  Cancel",
            command=self._cancel_scan,
            state=tk.DISABLED,
        )
        self._cancel_btn.pack(side=tk.LEFT)

        # ── Progress bar ────────────────────────────────────────────────
        progress_frame = ttk.Frame(self)
        progress_frame.pack(fill=tk.X, padx=8)

        self._progress_var = tk.DoubleVar(value=0)
        self._progress_bar = ttk.Progressbar(
            progress_frame,
            variable=self._progress_var,
            maximum=100,
            style="Horizontal.TProgressbar",
        )
        self._progress_bar.pack(fill=tk.X, pady=(0, 2))

        self._status_var = tk.StringVar(value="Ready.  Add directories and press Start Scan.")
        ttk.Label(progress_frame, textvariable=self._status_var).pack(anchor=tk.W)

        # ── Bottom panel: results tree ───────────────────────────────────
        results_frame = ttk.Frame(paned)
        paned.add(results_frame, weight=3)

        ttk.Label(results_frame, text="Results:").pack(anchor=tk.W)

        tree_frame = ttk.Frame(results_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("name", "size", "path", "modified")
        self._tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="tree headings",
            selectmode=tk.EXTENDED,
        )
        self._tree.heading("#0", text="Group")
        self._tree.heading("name", text="File Name")
        self._tree.heading("size", text="Size")
        self._tree.heading("path", text="Full Path")
        self._tree.heading("modified", text="Modified")

        self._tree.column("#0", width=110, stretch=False)
        self._tree.column("name", width=200)
        self._tree.column("size", width=90, anchor=tk.E)
        self._tree.column("path", width=430)
        self._tree.column("modified", width=150, anchor=tk.CENTER)

        tree_scroll_y = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self._tree.yview)
        tree_scroll_x = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self._tree.xview)
        self._tree.configure(
            yscrollcommand=tree_scroll_y.set,
            xscrollcommand=tree_scroll_x.set,
        )
        tree_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        tree_scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        self._tree.pack(fill=tk.BOTH, expand=True)

        self._tree.tag_configure("group", background="#e3f2fd", font=("Segoe UI", 9, "bold"))
        self._tree.tag_configure("dupe_odd", background=_CLR_DUPE_ODD)
        self._tree.tag_configure("dupe_even", background=_CLR_DUPE_EVEN)

        # ── Action bar ──────────────────────────────────────────────────
        action_frame = ttk.Frame(self)
        action_frame.pack(fill=tk.X, padx=8, pady=4)

        ttk.Button(
            action_frame, text="☑ Select All Duplicates",
            command=self._select_all_dupes,
        ).pack(side=tk.LEFT, padx=(0, 4))

        ttk.Button(
            action_frame, text="↕ Invert Selection",
            command=self._invert_selection,
        ).pack(side=tk.LEFT, padx=(0, 12))

        ttk.Button(
            action_frame, text="🗑  Delete Selected",
            style="Danger.TButton",
            command=self._delete_selected,
        ).pack(side=tk.LEFT, padx=(0, 4))

        ttk.Button(
            action_frame, text="📄  Export Report",
            command=self._export_report,
        ).pack(side=tk.LEFT)

        self._summary_var = tk.StringVar(value="")
        ttk.Label(
            action_frame, textvariable=self._summary_var,
            style="Summary.TLabel",
        ).pack(side=tk.RIGHT, padx=8)

    # ------------------------------------------------------------------
    # Directory management
    # ------------------------------------------------------------------

    def _add_directory(self) -> None:
        path = fd.askdirectory(title="Select a directory to scan")
        if path and path not in self._dir_listbox.get(0, tk.END):
            self._dir_listbox.insert(tk.END, path)

    def _remove_directories(self) -> None:
        for idx in reversed(self._dir_listbox.curselection()):
            self._dir_listbox.delete(idx)

    def _clear_directories(self) -> None:
        self._dir_listbox.delete(0, tk.END)

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def _start_scan(self) -> None:
        dirs = list(self._dir_listbox.get(0, tk.END))
        if not dirs:
            mb.showwarning("No directories", "Please add at least one directory to scan.")
            return

        # Clear previous results
        self._clear_tree()
        self._groups = []
        self._progress_var.set(0)
        self._summary_var.set("")
        self._status_var.set("Collecting files…")

        self._scan_btn.configure(state=tk.DISABLED)
        self._cancel_btn.configure(state=tk.NORMAL)

        self._scanner = DuplicateScanner(
            root_paths=dirs,
            progress_callback=self._on_progress,
            done_callback=self._on_done,
            error_callback=self._on_error,
        )
        self._scanner.start()

    def _cancel_scan(self) -> None:
        if self._scanner:
            self._scanner.cancel()
        self._status_var.set("Cancelling…")
        self._cancel_btn.configure(state=tk.DISABLED)

    # ── Callbacks from worker thread (must not touch tkinter directly) ──

    def _on_progress(self, scanned: int, total: int, path: str) -> None:
        pct = (scanned / total * 100) if total else 0
        self.after(0, self._update_progress, pct, scanned, total, path)

    def _on_done(self, groups: List[List[str]]) -> None:
        self.after(0, self._display_results, groups)

    def _on_error(self, exc: Exception) -> None:
        self.after(0, self._show_error, str(exc))

    # ── GUI-thread callbacks ────────────────────────────────────────────

    def _update_progress(self, pct: float, scanned: int, total: int, path: str) -> None:
        self._progress_var.set(pct)
        short = os.path.basename(path)
        self._status_var.set(f"Scanning {scanned}/{total}:  {short}")

    def _display_results(self, groups: List[List[str]]) -> None:
        self._groups = groups
        self._scan_btn.configure(state=tk.NORMAL)
        self._cancel_btn.configure(state=tk.DISABLED)
        self._progress_var.set(100)
        self._populate_tree()

        n_groups = len(groups)
        n_files = sum(len(g) for g in groups)
        waste = wasted_space(groups)
        if n_groups:
            self._status_var.set(
                f"Scan complete.  Found {n_groups} duplicate group(s) "
                f"({n_files} files total)."
            )
            self._summary_var.set(
                f"{n_groups} group(s) · {n_files} files · "
                f"{human_readable_size(waste)} wasted"
            )
        else:
            self._status_var.set("Scan complete.  No duplicates found.")
            self._summary_var.set("No duplicates found.")

    def _show_error(self, msg: str) -> None:
        self._scan_btn.configure(state=tk.NORMAL)
        self._cancel_btn.configure(state=tk.DISABLED)
        mb.showerror("Scan Error", msg)

    # ------------------------------------------------------------------
    # Tree management
    # ------------------------------------------------------------------

    def _clear_tree(self) -> None:
        for item in self._tree.get_children():
            self._tree.delete(item)
        self._item_path.clear()
        self._item_group.clear()

    def _populate_tree(self) -> None:
        self._clear_tree()
        for group_idx, paths in enumerate(self._groups):
            try:
                group_size = os.path.getsize(paths[0])
            except OSError:
                group_size = 0

            group_label = (
                f"Group {group_idx + 1}  "
                f"({len(paths)} files · {human_readable_size(group_size)} each)"
            )
            group_node = self._tree.insert(
                "",
                tk.END,
                text=group_label,
                values=("", "", "", ""),
                tags=("group",),
                open=True,
            )
            self._item_group[group_node] = group_idx

            for file_idx, path in enumerate(paths):
                name = os.path.basename(path)
                try:
                    size = os.path.getsize(path)
                    mtime = os.path.getmtime(path)
                    modified = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
                    size_str = human_readable_size(size)
                except OSError:
                    size_str = "N/A"
                    modified = "N/A"

                tag = "dupe_odd" if file_idx % 2 == 0 else "dupe_even"
                item = self._tree.insert(
                    group_node,
                    tk.END,
                    text="",
                    values=(name, size_str, path, modified),
                    tags=(tag,),
                )
                self._item_path[item] = path

    # ------------------------------------------------------------------
    # Selection helpers
    # ------------------------------------------------------------------

    def _select_all_dupes(self) -> None:
        """Select all file-level rows (leaving group headers unselected)."""
        self._tree.selection_set(list(self._item_path.keys()))

    def _invert_selection(self) -> None:
        current = set(self._tree.selection())
        all_file_items = set(self._item_path.keys())
        new_selection = all_file_items - current
        self._tree.selection_set(list(new_selection))

    # ------------------------------------------------------------------
    # Delete action
    # ------------------------------------------------------------------

    def _delete_selected(self) -> None:
        selected = [
            item for item in self._tree.selection()
            if item in self._item_path
        ]
        if not selected:
            mb.showinfo("Nothing selected", "Select file rows to delete.")
            return

        paths = [self._item_path[item] for item in selected]

        # Safety check: would we delete ALL copies of any group?
        groups_to_nuke: Dict[int, List[str]] = {}
        for item in selected:
            parent = self._tree.parent(item)
            gidx = self._item_group.get(parent)
            if gidx is None:
                continue
            groups_to_nuke.setdefault(gidx, []).append(item)

        for gidx, items in groups_to_nuke.items():
            group = self._groups[gidx]
            if len(items) >= len(group):
                mb.showwarning(
                    "Unsafe deletion",
                    f"You selected ALL copies in Group {gidx + 1}.\n"
                    "Keep at least one copy.  Aborting.",
                )
                return

        answer = mb.askyesno(
            "Confirm deletion",
            f"Permanently delete {len(paths)} file(s)?\n\nThis cannot be undone.",
            icon="warning",
        )
        if not answer:
            return

        errors: List[str] = []
        for path in paths:
            try:
                os.remove(path)
            except OSError as exc:
                errors.append(f"{path}: {exc}")

        # Remove deleted items from the tree
        for item in selected:
            if item in self._item_path:
                del self._item_path[item]
            self._tree.delete(item)

        # Remove empty group nodes
        for group_node in list(self._item_group.keys()):
            if not self._tree.get_children(group_node):
                del self._item_group[group_node]
                self._tree.delete(group_node)

        if errors:
            mb.showerror("Some files could not be deleted", "\n".join(errors))
        else:
            deleted = len(paths)
            mb.showinfo("Done", f"{deleted} file(s) deleted successfully.")

    # ------------------------------------------------------------------
    # Export report
    # ------------------------------------------------------------------

    def _export_report(self) -> None:
        if not self._groups:
            mb.showinfo("No results", "Run a scan first.")
            return

        path = fd.asksaveasfilename(
            title="Save Report",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return

        lines: List[str] = [
            "DupliCheck – Duplicate File Report",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Directories scanned: {', '.join(self._dir_listbox.get(0, tk.END))}",
            "",
            f"Total duplicate groups: {len(self._groups)}",
            f"Total wasted space: {human_readable_size(wasted_space(self._groups))}",
            "=" * 60,
            "",
        ]
        for idx, group in enumerate(self._groups, 1):
            try:
                sz = human_readable_size(os.path.getsize(group[0]))
            except OSError:
                sz = "?"
            lines.append(f"Group {idx}  ({len(group)} copies · {sz} each)")
            for p in group:
                try:
                    mtime = datetime.fromtimestamp(os.path.getmtime(p)).strftime(
                        "%Y-%m-%d %H:%M"
                    )
                except OSError:
                    mtime = "?"
                lines.append(f"  [{mtime}]  {p}")
            lines.append("")

        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines))
            mb.showinfo("Exported", f"Report saved to:\n{path}")
        except OSError as exc:
            mb.showerror("Export failed", str(exc))


def main() -> None:
    """Launch the DupliCheck GUI application."""
    app = DupliCheckApp()
    app.mainloop()
