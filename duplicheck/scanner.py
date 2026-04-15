"""
duplicheck.scanner
==================
Deep duplicate-file detection engine.

Algorithm (three-stage pipeline)
---------------------------------
1. **Size filter** – files are grouped by their exact byte-size.
   Any group with only one member cannot have a duplicate and is
   immediately discarded.  This is an O(n) pass and typically
   eliminates > 95 % of candidates.

2. **Partial-hash filter** – the first ``PARTIAL_READ_BYTES`` (64 KiB by
   default) of each candidate file are hashed with SHA-256.  Files whose
   partial hash is unique are again discarded.  This catches the common
   case where two same-size files differ right at the start.

3. **Full-hash comparison** – the remaining candidates are hashed in full
   with SHA-256.  Files that share a full hash are true duplicates.

The engine is thread-safe: scanning runs in a background
:class:`threading.Thread` and communicates progress/results to the caller
via optional callback functions.

Usage
-----
::

    from duplicheck.scanner import DuplicateScanner

    def on_progress(scanned, total, current_path):
        print(f"{scanned}/{total}: {current_path}")

    def on_done(groups):
        for paths in groups:
            print("Duplicate group:")
            for p in paths:
                print("  ", p)

    scanner = DuplicateScanner(
        root_paths=["/home/user/Documents"],
        progress_callback=on_progress,
        done_callback=on_done,
    )
    scanner.start()   # non-blocking; runs in a background thread
    scanner.join()    # wait for completion (optional)

Alternatively use the convenience function::

    from duplicheck.scanner import find_duplicates
    groups = find_duplicates("/home/user/Documents")
"""

from __future__ import annotations

import hashlib
import os
import threading
from collections import defaultdict
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

# Number of bytes read for the *partial* hash stage.
PARTIAL_READ_BYTES: int = 65_536  # 64 KiB


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _sha256_partial(path: str, read_bytes: int = PARTIAL_READ_BYTES) -> Optional[str]:
    """Return the SHA-256 hex-digest of the first *read_bytes* of *path*.

    Returns ``None`` if the file cannot be read (e.g. permission error).
    """
    hasher = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            hasher.update(fh.read(read_bytes))
    except OSError:
        return None
    return hasher.hexdigest()


def _sha256_full(path: str) -> Optional[str]:
    """Return the SHA-256 hex-digest of the entire content of *path*.

    Reads in chunks of 1 MiB to keep memory usage bounded.
    Returns ``None`` if the file cannot be read.
    """
    hasher = hashlib.sha256()
    chunk_size = 1_048_576  # 1 MiB
    try:
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(chunk_size)
                if not chunk:
                    break
                hasher.update(chunk)
    except OSError:
        return None
    return hasher.hexdigest()


def _iter_files(root_paths: Iterable[str]) -> List[str]:
    """Recursively yield every regular file under each path in *root_paths*.

    Symbolic links are *not* followed to avoid infinite loops.
    """
    files: List[str] = []
    for root in root_paths:
        root = os.path.abspath(root)
        if os.path.isfile(root):
            files.append(root)
        elif os.path.isdir(root):
            for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
                for name in filenames:
                    files.append(os.path.join(dirpath, name))
    return files


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def find_duplicates(
    *root_paths: str,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> List[List[str]]:
    """Synchronously find all duplicate files under *root_paths*.

    Parameters
    ----------
    *root_paths:
        One or more directory or file paths to scan.
    progress_callback:
        Optional callable invoked as ``progress_callback(scanned, total,
        current_path)`` after each file is fully processed.

    Returns
    -------
    list of list of str
        Each inner list contains the absolute paths of one duplicate group
        (≥ 2 files with identical content).  Returned in descending order
        of group size.
    """
    scanner = DuplicateScanner(
        root_paths=list(root_paths),
        progress_callback=progress_callback,
    )
    return scanner.run_sync()


class DuplicateScanner:
    """Background-thread duplicate-file scanner.

    Parameters
    ----------
    root_paths:
        Directories (or individual files) to scan.
    progress_callback:
        Called as ``progress_callback(scanned, total, current_path)`` from
        the worker thread after every file is processed.
    done_callback:
        Called as ``done_callback(groups)`` from the worker thread when
        scanning completes.  *groups* is the same list-of-lists returned by
        :meth:`run_sync`.
    error_callback:
        Called as ``error_callback(exc)`` if an unexpected exception is
        raised during scanning.
    min_file_size:
        Files strictly smaller than this value (bytes) are ignored.
        Defaults to 1 – zero-byte files are excluded because they are
        trivially identical and rarely meaningful.
    """

    def __init__(
        self,
        root_paths: Sequence[str],
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        done_callback: Optional[Callable[[List[List[str]]], None]] = None,
        error_callback: Optional[Callable[[Exception], None]] = None,
        min_file_size: int = 1,
    ) -> None:
        self.root_paths = list(root_paths)
        self.progress_callback = progress_callback
        self.done_callback = done_callback
        self.error_callback = error_callback
        self.min_file_size = min_file_size

        self._thread: Optional[threading.Thread] = None
        self._cancel_event = threading.Event()
        self._result: Optional[List[List[str]]] = None

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start scanning in a background thread (non-blocking)."""
        self._cancel_event.clear()
        self._result = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        """Request cancellation.  The background thread will stop soon."""
        self._cancel_event.set()

    def join(self, timeout: Optional[float] = None) -> None:
        """Block until the background thread finishes."""
        if self._thread is not None:
            self._thread.join(timeout)

    @property
    def is_running(self) -> bool:
        """``True`` while the background thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    @property
    def result(self) -> Optional[List[List[str]]]:
        """The duplicate groups found, or ``None`` if not yet complete."""
        return self._result

    def run_sync(self) -> List[List[str]]:
        """Run the scan synchronously in the calling thread.

        Returns
        -------
        list of list of str
        """
        self._cancel_event.clear()
        self._run()
        return self._result or []

    # ------------------------------------------------------------------
    # Internal scanning logic
    # ------------------------------------------------------------------

    def _run(self) -> None:
        try:
            groups = self._scan()
            self._result = groups
            if self.done_callback:
                self.done_callback(groups)
        except Exception as exc:  # noqa: BLE001
            if self.error_callback:
                self.error_callback(exc)

    def _scan(self) -> List[List[str]]:
        # ── Stage 0: collect all files ─────────────────────────────────
        all_files = _iter_files(self.root_paths)

        # Filter by minimum size
        candidates: List[Tuple[int, str]] = []
        for path in all_files:
            if self._cancel_event.is_set():
                return []
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if size >= self.min_file_size:
                candidates.append((size, path))

        total = len(candidates)

        # ── Stage 1: group by size ──────────────────────────────────────
        size_groups: Dict[int, List[str]] = defaultdict(list)
        for size, path in candidates:
            size_groups[size].append(path)

        # Keep only groups with ≥ 2 files
        size_candidates = [paths for paths in size_groups.values() if len(paths) > 1]

        # ── Stage 2: partial-hash filter ───────────────────────────────
        scanned = 0
        partial_groups: Dict[str, List[str]] = defaultdict(list)

        for group in size_candidates:
            for path in group:
                if self._cancel_event.is_set():
                    return []
                digest = _sha256_partial(path)
                scanned += 1
                if self.progress_callback:
                    self.progress_callback(scanned, total, path)
                if digest is not None:
                    partial_groups[digest].append(path)

        partial_candidates = [
            paths for paths in partial_groups.values() if len(paths) > 1
        ]

        # ── Stage 3: full-hash confirmation ────────────────────────────
        full_groups: Dict[str, List[str]] = defaultdict(list)

        for group in partial_candidates:
            for path in group:
                if self._cancel_event.is_set():
                    return []
                digest = _sha256_full(path)
                if digest is not None:
                    full_groups[digest].append(path)

        # Build result: only real duplicates (≥ 2 identical files)
        duplicates = [
            sorted(paths)
            for paths in full_groups.values()
            if len(paths) > 1
        ]
        # Sort by group size (largest groups first), then by first path
        duplicates.sort(key=lambda g: (-len(g), g[0]))
        return duplicates


# ---------------------------------------------------------------------------
# Utility helpers (used by the GUI)
# ---------------------------------------------------------------------------


def human_readable_size(num_bytes: int) -> str:
    """Convert *num_bytes* to a human-readable string (e.g. ``'4.2 MiB'``)."""
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:,.1f} {unit}"
        num_bytes /= 1024.0  # type: ignore[assignment]
    return f"{num_bytes:,.1f} PiB"


def wasted_space(groups: List[List[str]]) -> int:
    """Return total bytes wasted by keeping one copy of each group."""
    total = 0
    for group in groups:
        try:
            size = os.path.getsize(group[0])
        except OSError:
            continue
        # We keep one copy, the rest (len-1) are wasted
        total += size * (len(group) - 1)
    return total
