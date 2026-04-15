"""
Unit tests for duplicheck.scanner
==================================
Tests cover:
- Stage 1 (size grouping)
- Stage 2 (partial-hash filtering)
- Stage 3 (full-hash confirmation)
- Edge cases: empty directory, single files, zero-byte files, permission errors
- Utility functions: human_readable_size, wasted_space
- DuplicateScanner callbacks and cancellation
"""

from __future__ import annotations

import os
import threading
import time
import unittest
import tempfile

from duplicheck.scanner import (
    DuplicateScanner,
    find_duplicates,
    human_readable_size,
    wasted_space,
    _sha256_partial,
    _sha256_full,
    _iter_files,
)


def _write(path: str, content: bytes) -> str:
    """Write *content* to *path* and return *path*."""
    with open(path, "wb") as fh:
        fh.write(content)
    return path


class TestIterFiles(unittest.TestCase):
    def test_finds_all_files_recursively(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(os.path.join(tmp, "a.txt"), b"hello")
            sub = os.path.join(tmp, "sub")
            os.makedirs(sub)
            _write(os.path.join(sub, "b.txt"), b"world")

            files = _iter_files([tmp])
            self.assertEqual(len(files), 2)
            basenames = {os.path.basename(f) for f in files}
            self.assertIn("a.txt", basenames)
            self.assertIn("b.txt", basenames)

    def test_single_file_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(os.path.join(tmp, "lone.txt"), b"x")
            files = _iter_files([p])
            self.assertEqual(files, [p])

    def test_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = _iter_files([tmp])
            self.assertEqual(files, [])

    def test_nonexistent_path_ignored(self):
        files = _iter_files(["/this/does/not/exist/at/all"])
        self.assertEqual(files, [])


class TestHashHelpers(unittest.TestCase):
    def test_partial_hash_consistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(os.path.join(tmp, "f.bin"), b"A" * 200)
            h1 = _sha256_partial(p)
            h2 = _sha256_partial(p)
            self.assertEqual(h1, h2)
            self.assertIsNotNone(h1)

    def test_full_hash_consistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(os.path.join(tmp, "f.bin"), b"B" * 5000)
            h1 = _sha256_full(p)
            h2 = _sha256_full(p)
            self.assertEqual(h1, h2)

    def test_different_content_different_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            p1 = _write(os.path.join(tmp, "a.bin"), b"content-A")
            p2 = _write(os.path.join(tmp, "b.bin"), b"content-B")
            self.assertNotEqual(_sha256_full(p1), _sha256_full(p2))

    def test_same_content_same_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = b"identical content"
            p1 = _write(os.path.join(tmp, "a.bin"), data)
            p2 = _write(os.path.join(tmp, "b.bin"), data)
            self.assertEqual(_sha256_full(p1), _sha256_full(p2))

    def test_missing_file_returns_none(self):
        self.assertIsNone(_sha256_partial("/no/such/file"))
        self.assertIsNone(_sha256_full("/no/such/file"))


class TestFindDuplicates(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ── helpers ──────────────────────────────────────────────────────────

    def _path(self, name: str) -> str:
        return os.path.join(self._tmp, name)

    # ── tests ────────────────────────────────────────────────────────────

    def test_no_duplicates(self):
        _write(self._path("a.txt"), b"alpha")
        _write(self._path("b.txt"), b"beta")
        groups = find_duplicates(self._tmp)
        self.assertEqual(groups, [])

    def test_simple_duplicate_pair(self):
        data = b"same content here"
        _write(self._path("copy1.txt"), data)
        _write(self._path("copy2.txt"), data)
        _write(self._path("unique.txt"), b"different")

        groups = find_duplicates(self._tmp)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 2)

    def test_multiple_groups(self):
        _write(self._path("a1.bin"), b"group-A")
        _write(self._path("a2.bin"), b"group-A")
        _write(self._path("b1.bin"), b"group-B-different")
        _write(self._path("b2.bin"), b"group-B-different")

        groups = find_duplicates(self._tmp)
        self.assertEqual(len(groups), 2)
        sizes = sorted(len(g) for g in groups)
        self.assertEqual(sizes, [2, 2])

    def test_triple_duplicate(self):
        data = b"three of me"
        for name in ("x.dat", "y.dat", "z.dat"):
            _write(self._path(name), data)

        groups = find_duplicates(self._tmp)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 3)

    def test_zero_byte_files_excluded(self):
        _write(self._path("empty1.txt"), b"")
        _write(self._path("empty2.txt"), b"")
        groups = find_duplicates(self._tmp)
        self.assertEqual(groups, [])

    def test_same_size_different_content(self):
        # Files with same size but different content must NOT be duplicates
        _write(self._path("f1.txt"), b"AAAA")
        _write(self._path("f2.txt"), b"BBBB")
        groups = find_duplicates(self._tmp)
        self.assertEqual(groups, [])

    def test_large_file_duplicates(self):
        data = b"X" * 200_000  # 200 KiB > PARTIAL_READ_BYTES
        _write(self._path("big1.bin"), data)
        _write(self._path("big2.bin"), data)

        groups = find_duplicates(self._tmp)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 2)

    def test_large_file_different_tails(self):
        # Files differ only in the last byte (beyond partial-read window)
        base = b"Y" * 200_000
        _write(self._path("t1.bin"), base + b"\x00")
        _write(self._path("t2.bin"), base + b"\xff")

        groups = find_duplicates(self._tmp)
        self.assertEqual(groups, [])

    def test_recursive_subdirectories(self):
        sub = os.path.join(self._tmp, "sub")
        os.makedirs(sub)
        data = b"deep duplicate"
        _write(self._path("top.txt"), data)
        _write(os.path.join(sub, "bottom.txt"), data)

        groups = find_duplicates(self._tmp)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 2)

    def test_progress_callback_called(self):
        data = b"callback test"
        _write(self._path("c1.bin"), data)
        _write(self._path("c2.bin"), data)

        calls = []
        find_duplicates(self._tmp, progress_callback=lambda s, t, p: calls.append(s))
        self.assertGreater(len(calls), 0)

    def test_multiple_root_paths(self):
        with tempfile.TemporaryDirectory() as tmp2:
            data = b"across roots"
            _write(self._path("r1.txt"), data)
            _write(os.path.join(tmp2, "r2.txt"), data)

            groups = find_duplicates(self._tmp, tmp2)
            self.assertEqual(len(groups), 1)

    def test_empty_directory(self):
        groups = find_duplicates(self._tmp)
        self.assertEqual(groups, [])


class TestDuplicateScannerAsync(unittest.TestCase):
    def test_done_callback_invoked(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = b"async test"
            _write(os.path.join(tmp, "p1.bin"), data)
            _write(os.path.join(tmp, "p2.bin"), data)

            results = []
            evt = threading.Event()

            def on_done(groups):
                results.append(groups)
                evt.set()

            scanner = DuplicateScanner(root_paths=[tmp], done_callback=on_done)
            scanner.start()
            evt.wait(timeout=10)

            self.assertTrue(evt.is_set(), "done_callback was not called in time")
            self.assertEqual(len(results[0]), 1)

    def test_cancel_stops_scan(self):
        # Create many files so the scan takes a moment
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(50):
                _write(os.path.join(tmp, f"f{i}.bin"), b"data" * 1000)

            done = threading.Event()
            scanner = DuplicateScanner(
                root_paths=[tmp],
                done_callback=lambda g: done.set(),
            )
            scanner.start()
            scanner.cancel()
            scanner.join(timeout=5)
            # After cancellation the scanner should not be running
            self.assertFalse(scanner.is_running)

    def test_error_callback_invoked(self):
        errors = []
        evt = threading.Event()

        class BrokenScanner(DuplicateScanner):
            def _scan(self):
                raise RuntimeError("deliberate error")

        def on_error(exc):
            errors.append(exc)
            evt.set()

        scanner = BrokenScanner(root_paths=["/tmp"], error_callback=on_error)
        scanner.start()
        evt.wait(timeout=5)
        self.assertTrue(evt.is_set())
        self.assertIsInstance(errors[0], RuntimeError)


class TestUtilities(unittest.TestCase):
    def test_human_readable_size_bytes(self):
        self.assertEqual(human_readable_size(500), "500.0 B")

    def test_human_readable_size_kib(self):
        result = human_readable_size(2048)
        self.assertIn("KiB", result)

    def test_human_readable_size_mib(self):
        result = human_readable_size(1_048_576)
        self.assertIn("MiB", result)

    def test_human_readable_size_gib(self):
        result = human_readable_size(1_073_741_824)
        self.assertIn("GiB", result)

    def test_wasted_space_single_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = b"X" * 1000
            p1 = _write(os.path.join(tmp, "a.bin"), data)
            p2 = _write(os.path.join(tmp, "b.bin"), data)
            groups = [[p1, p2]]
            # wasted = 1 × 1000 bytes (we keep one, waste one)
            self.assertEqual(wasted_space(groups), 1000)

    def test_wasted_space_no_groups(self):
        self.assertEqual(wasted_space([]), 0)

    def test_wasted_space_triple_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = b"Y" * 512
            paths = [_write(os.path.join(tmp, f"f{i}.bin"), data) for i in range(3)]
            groups = [paths]
            # wasted = 2 × 512
            self.assertEqual(wasted_space(groups), 1024)


if __name__ == "__main__":
    unittest.main()
