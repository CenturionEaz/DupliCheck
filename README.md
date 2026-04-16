# DupliCheck – Duplicate File Finder

![DupliCheck GUI](https://github.com/user-attachments/assets/ec7d87b6-4920-401e-ad3d-c7737545c968)

A Python application with a clean GUI that scans your system and finds all
duplicate files of every type using a deep, multi-stage detection algorithm.
No extra dependencies – just Python 3.8+ and the built-in `tkinter` library.

---

## Features

| Feature | Details |
|---|---|
| **Deep detection** | Three-stage pipeline: size filter → partial SHA-256 → full SHA-256 |
| **All file types** | Text, binary, images, videos, archives – everything is compared by content |
| **Fast** | Size grouping eliminates > 95 % of candidates before any hashing |
| **GUI** | Tkinter-based interface with progress bar, grouped results, and action buttons |
| **Safe deletion** | Prevents deleting all copies of a group; confirms before any file removal |
| **Export** | Save a plain-text report of all duplicate groups |
| **Background scan** | Scanning runs in a worker thread; the UI stays responsive |
| **Cancellable** | Cancel a long scan at any time |

---

## Requirements

- Python 3.8 or newer
- `tkinter` (bundled with most Python distributions)

On **Debian / Ubuntu** you may need to install tkinter separately:

```bash
sudo apt-get install python3-tk
```

---

## Installation

No installation required – just clone and run:

```bash
git clone https://github.com/CenturionEaz/DupliCheck.git
cd DupliCheck
python main.py
```

---

## Usage

### GUI

```bash
python main.py
```

1. Click **➕ Add Directory** to add one or more directories to scan.
2. Click **▶ Start Scan** – a progress bar tracks each file as it is processed.
3. The **Results** panel lists every duplicate group with file name, size, full
   path, and last-modified date.
4. Use **☑ Select All Duplicates** / **↕ Invert Selection** to choose which
   copies to remove.
5. Click **🗑 Delete Selected** to permanently remove the selected files
   (DupliCheck will warn you if you are about to delete *all* copies of a
   group).
6. Optionally, click **📄 Export Report** to save a plain-text summary.

### Python API

```python
from duplicheck.scanner import find_duplicates, human_readable_size, wasted_space

# Synchronous scan of one or more directories
groups = find_duplicates("/home/user/Documents", "/home/user/Downloads")

for i, paths in enumerate(groups, 1):
    print(f"Group {i}:")
    for p in paths:
        print(f"  {p}")

print(f"Wasted space: {human_readable_size(wasted_space(groups))}")
```

#### `DuplicateScanner` (background thread)

```python
from duplicheck.scanner import DuplicateScanner

scanner = DuplicateScanner(
    root_paths=["/home/user"],
    progress_callback=lambda scanned, total, path: print(f"{scanned}/{total}"),
    done_callback=lambda groups: print(f"Found {len(groups)} groups"),
)
scanner.start()   # non-blocking
scanner.join()    # wait for completion
```

---

## How the Detection Works

DupliCheck uses a **three-stage pipeline** to find duplicates accurately and
efficiently:

```
All files on disk
      │
      ▼
┌─────────────────────────────────────┐
│  Stage 1 – Size grouping            │  O(n), no I/O beyond stat()
│  Files with a unique size are        │
│  immediately discarded.             │
└──────────────┬──────────────────────┘
               │ candidates with same size
               ▼
┌─────────────────────────────────────┐
│  Stage 2 – Partial SHA-256          │  Read first 64 KiB per file
│  Files whose first 64 KiB differ    │
│  are discarded.                     │
└──────────────┬──────────────────────┘
               │ candidates with same partial hash
               ▼
┌─────────────────────────────────────┐
│  Stage 3 – Full SHA-256             │  Read entire file
│  Files with the same full hash are  │
│  confirmed duplicates.              │
└─────────────────────────────────────┘
```

Zero-byte files are excluded by default (they are trivially identical and
rarely meaningful).  Symbolic links are never followed.

---

## Project Structure

```
DupliCheck/
├── main.py                  # Entry point – launches the GUI
├── requirements.txt         # No third-party dependencies
├── duplicheck/
│   ├── __init__.py          # Package metadata
│   ├── scanner.py           # Duplicate detection engine + utilities
│   └── gui.py               # Tkinter GUI
└── tests/
    ├── __init__.py
    └── test_scanner.py      # 31 unit tests for the scanner
```

---

## Running the Tests

```bash
python -m unittest discover -s tests -v
```

All 31 tests cover:

- File collection (`_iter_files`)
- Hash helpers (`_sha256_partial`, `_sha256_full`)
- All three detection stages
- Edge cases: empty dirs, zero-byte files, same-size / different-content files,
  large files (> 64 KiB), cross-directory duplicates
- Async scanner: callbacks, cancellation, error handling
- Utility functions: `human_readable_size`, `wasted_space`

---

## License

MIT
