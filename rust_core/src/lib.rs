//! # rust_core: the native half of sift
//!
//! Python imports this crate as `sift._rust_core`. It owns the two jobs where
//! Rust buys a real speed-up over pure Python:
//!
//! 1. walking a directory tree in parallel ([`walker`])
//! 2. splitting Markdown / source files into heading-aware chunks ([`chunker`])
//!
//! ## Who owns what
//!
//! | File         | Status                                                          |
//! |--------------|-----------------------------------------------------------------|
//! | `lib.rs`     | **done**: PyO3 surface (class, arg validation, errors, GIL)      |
//! | `walker.rs`  | **`todo!()`**: the parallel directory walker (step R1)          |
//! | `chunker.rs` | **`todo!()`**: heading-aware recursive chunking (steps R2, R3)  |
//!
//! ## Layering rule
//!
//! Only this file talks to the Python interpreter. `walker.rs` and `chunker.rs`
//! are plain Rust: no `Python<'py>` token, no `PyResult`, no `Bound<'py, T>`.
//! That keeps the algorithms testable with `cargo test` and means everything
//! you write there can run with the GIL released.
//!
//! ## Python-visible API (mirrored in `sift/_rust_core.pyi`)
//!
//! ```python
//! DocumentChunk(file_path, chunk_index, text, start_line, heading_context)
//! walk_files(root, extensions=None) -> list[str]
//! chunk_text(file_path, text, max_chars=1200, overlap=150) -> list[DocumentChunk]
//! chunk_directory(root, extensions=None, max_chars=1200, overlap=150) -> list[DocumentChunk]
//! ```

use std::path::{Path, PathBuf};

use pyo3::exceptions::{PyFileNotFoundError, PyNotADirectoryError, PyValueError};
use pyo3::prelude::*;

pub mod chunker;
pub mod walker;

use chunker::ChunkConfig;

/// Default maximum chunk size, in Unicode scalar values (`char`s, not bytes).
///
/// ~1200 characters is roughly 250 word-pieces, which fits the 256-token window
/// of `all-MiniLM-L6-v2` without truncation.
pub const DEFAULT_MAX_CHARS: usize = 1200;

/// Default number of characters shared between consecutive pieces of one
/// oversized section.
pub const DEFAULT_OVERLAP: usize = 150;

/// File extensions indexed when the caller does not pass any.
pub const DEFAULT_EXTENSIONS: [&str; 2] = ["md", "markdown"];

// ---------------------------------------------------------------------------
// DocumentChunk
// ---------------------------------------------------------------------------

/// One retrievable unit of text: the thing that gets embedded, scored by BM25,
/// stored as a row in SQLite, and shown to the user as a search hit.
///
/// `#[pyclass]` options used here:
///
/// * `module = "sift._rust_core"`: so `repr(type(chunk))` and pickling point
///   at the real import path instead of `builtins`.
/// * `frozen`: instances are immutable from Python. PyO3 can then skip its
///   runtime borrow checker, and the class is safe to share across threads.
/// * `get_all`: generate a read-only Python property for every field.
/// * `eq`: Python `==` delegates to the derived `PartialEq`.
///
/// Not used: `from_py_object`. Without it PyO3 (>= 0.28) does not implement
/// `FromPyObject` for the class, which is what we want: no function here takes
/// a `DocumentChunk` argument by value. To *borrow* one from Python you would
/// accept `&Bound<'_, DocumentChunk>` or `PyRef<'_, DocumentChunk>` instead.
#[pyclass(module = "sift._rust_core", frozen, get_all, eq)]
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DocumentChunk {
    /// Path of the source file, exactly as produced by the walker
    /// (`root` joined with the path relative to it).
    pub file_path: String,
    /// 0-based position of this chunk within its file.
    pub chunk_index: usize,
    /// The chunk's text. Includes the heading line when the chunk opens a section.
    pub text: String,
    /// 1-based line number, in the original file, of the first line of `text`.
    pub start_line: usize,
    /// Breadcrumb of the Markdown headings in scope where the chunk starts,
    /// joined with `" > "`, e.g. `"Virtual Memory > Paging > The TLB"`.
    /// Empty for text that precedes the first heading and for non-Markdown files.
    pub heading_context: String,
}

#[pymethods]
impl DocumentChunk {
    /// Python-side constructor. Production chunks are built in Rust; this
    /// exists so tests (and the Python fallback chunker) can make instances.
    #[new]
    fn py_new(
        file_path: String,
        chunk_index: usize,
        text: String,
        start_line: usize,
        heading_context: String,
    ) -> Self {
        Self {
            file_path,
            chunk_index,
            text,
            start_line,
            heading_context,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "DocumentChunk(file_path={:?}, chunk_index={}, start_line={}, heading_context={:?}, text=<{} chars>)",
            self.file_path,
            self.chunk_index,
            self.start_line,
            self.heading_context,
            self.text.chars().count(),
        )
    }

    /// `len(chunk)` is the number of characters in `text` (not UTF-8 bytes).
    fn __len__(&self) -> usize {
        self.text.chars().count()
    }
}

// ---------------------------------------------------------------------------
// Argument handling shared by the #[pyfunction]s (plain Rust, unit-tested below)
// ---------------------------------------------------------------------------

/// `[".MD", "markdown"]` -> `["md", "markdown"]`; `None` / `[]` -> defaults.
fn normalize_extensions(extensions: Option<Vec<String>>) -> Vec<String> {
    let mut exts: Vec<String> = extensions
        .unwrap_or_default()
        .iter()
        .map(|e| e.trim().trim_start_matches('.').to_ascii_lowercase())
        .filter(|e| !e.is_empty())
        .collect();
    if exts.is_empty() {
        exts = DEFAULT_EXTENSIONS.iter().map(|e| e.to_string()).collect();
    }
    exts.sort();
    exts.dedup();
    exts
}

/// Raise the same exceptions `os.scandir` would, before any Rust work starts.
fn validate_root(root: &Path) -> PyResult<()> {
    if !root.exists() {
        return Err(PyFileNotFoundError::new_err(format!(
            "no such directory: {}",
            root.display()
        )));
    }
    if !root.is_dir() {
        return Err(PyNotADirectoryError::new_err(format!(
            "not a directory: {}",
            root.display()
        )));
    }
    Ok(())
}

/// Validate chunk sizes and surface problems as Python `ValueError`.
fn chunk_config(max_chars: usize, overlap: usize) -> PyResult<ChunkConfig> {
    ChunkConfig::new(max_chars, overlap).map_err(PyValueError::new_err)
}

// ---------------------------------------------------------------------------
// Python-callable functions
// ---------------------------------------------------------------------------

/// Recursively list the files under `root` whose extension is in `extensions`.
///
/// Returns paths as strings, sorted ascending. `extensions` defaults to
/// `["md", "markdown"]`; a leading dot and upper case are tolerated.
///
/// Raises `FileNotFoundError` / `NotADirectoryError` for a bad `root`.
#[pyfunction]
#[pyo3(signature = (root, extensions=None))]
fn walk_files(
    py: Python<'_>,
    root: PathBuf,
    extensions: Option<Vec<String>>,
) -> PyResult<Vec<String>> {
    validate_root(&root)?;
    let extensions = normalize_extensions(extensions);

    // `detach` releases the GIL for the duration of the closure, so other
    // Python threads keep running while Rust walks the tree. The closure may
    // not touch any Python object, which is exactly why walker.rs is plain Rust.
    // The trailing `?` converts `std::io::Error` into the matching `OSError`
    // subclass via PyO3's `From<io::Error> for PyErr`.
    let paths = py.detach(|| walker::walk_files(&root, &extensions))?;

    Ok(paths
        .iter()
        .map(|path| path.to_string_lossy().into_owned())
        .collect())
}

/// Split one in-memory document into chunks.
///
/// `file_path` is only used to label the chunks and to decide whether Markdown
/// heading detection applies; nothing is read from disk. Handy for tests and
/// for chunking text that does not live in a file.
///
/// Raises `ValueError` if `max_chars == 0` or `overlap >= max_chars`.
#[pyfunction]
#[pyo3(signature = (file_path, text, max_chars=DEFAULT_MAX_CHARS, overlap=DEFAULT_OVERLAP))]
fn chunk_text(
    file_path: &str,
    text: &str,
    max_chars: usize,
    overlap: usize,
) -> PyResult<Vec<DocumentChunk>> {
    let config = chunk_config(max_chars, overlap)?;
    // No `detach` here: one document is microseconds of work, cheaper than
    // releasing and re-acquiring the GIL.
    Ok(chunker::chunk_document(file_path, text, &config))
}

/// Walk `root`, then read and chunk every matching file in parallel.
///
/// This is the call `sift index` makes. Output is deterministic: files in
/// sorted path order, chunks in `chunk_index` order within each file.
///
/// Raises `FileNotFoundError` / `NotADirectoryError` for a bad `root` and
/// `ValueError` for bad chunk sizes.
#[pyfunction]
#[pyo3(signature = (root, extensions=None, max_chars=DEFAULT_MAX_CHARS, overlap=DEFAULT_OVERLAP))]
fn chunk_directory(
    py: Python<'_>,
    root: PathBuf,
    extensions: Option<Vec<String>>,
    max_chars: usize,
    overlap: usize,
) -> PyResult<Vec<DocumentChunk>> {
    validate_root(&root)?;
    let extensions = normalize_extensions(extensions);
    let config = chunk_config(max_chars, overlap)?;

    // Everything inside the closure runs without the GIL, so other Python
    // threads stay responsive and any worker threads started inside it can
    // never wait on a GIL that this thread is holding.
    let chunks = py.detach(|| -> std::io::Result<Vec<DocumentChunk>> {
        let paths = walker::walk_files(&root, &extensions)?;
        Ok(chunker::chunk_files(&paths, &config))
    })?;

    // Returning `Vec<DocumentChunk>` makes PyO3 allocate one Python object per
    // chunk and collect them into a `list`. That conversion happens here, after
    // the GIL has been re-acquired.
    Ok(chunks)
}

// ---------------------------------------------------------------------------
// Module definition
// ---------------------------------------------------------------------------

/// Native core of sift: parallel file walking and heading-aware chunking.
///
/// The Rust module name below must equal `[lib] name` in Cargo.toml and the
/// last segment of `module-name` in pyproject.toml, otherwise Python fails with
/// "dynamic module does not define module export function (PyInit__rust_core)".
#[pymodule]
mod _rust_core {
    use pyo3::prelude::*;

    #[pymodule_export]
    use super::DocumentChunk;

    #[pymodule_export]
    use super::{chunk_directory, chunk_text, walk_files};

    #[pymodule_export]
    const DEFAULT_MAX_CHARS: usize = super::DEFAULT_MAX_CHARS;

    #[pymodule_export]
    const DEFAULT_OVERLAP: usize = super::DEFAULT_OVERLAP;

    #[pymodule_init]
    fn init(module: &Bound<'_, PyModule>) -> PyResult<()> {
        module.add("__version__", env!("CARGO_PKG_VERSION"))?;
        module.add("DEFAULT_EXTENSIONS", super::DEFAULT_EXTENSIONS.to_vec())?;
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Tests for the glue in this file. Run with `cargo test`.
// (Tests for *your* code belong in walker.rs / chunker.rs.)
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extensions_default_when_missing_or_empty() {
        assert_eq!(normalize_extensions(None), vec!["markdown", "md"]);
        assert_eq!(normalize_extensions(Some(vec![])), vec!["markdown", "md"]);
        assert_eq!(
            normalize_extensions(Some(vec!["  ".into(), ".".into()])),
            vec!["markdown", "md"]
        );
    }

    #[test]
    fn extensions_are_lowercased_undotted_and_deduplicated() {
        let given = Some(vec![".MD".into(), "md".into(), "Rs".into(), " .py ".into()]);
        assert_eq!(normalize_extensions(given), vec!["md", "py", "rs"]);
    }

    #[test]
    fn chunk_config_rejects_bad_sizes() {
        assert!(ChunkConfig::new(0, 0).is_err());
        assert!(ChunkConfig::new(100, 100).is_err());
        assert!(ChunkConfig::new(100, 250).is_err());
        let config = ChunkConfig::new(100, 20).expect("valid sizes");
        assert_eq!((config.max_chars, config.overlap), (100, 20));
    }
}
