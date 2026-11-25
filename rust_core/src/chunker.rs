//! Document chunking. **Yours to implement.**
//!
//! Specification: `IMPLEMENTATION_GUIDE.md`, sections 8 and 9.
//! Acceptance tests: `tests/test_rust_contract.py::TestChunkText` and
//! `::TestChunkDirectory` (they skip themselves until the `todo!()`s are gone).

// TODO(you): delete this attribute once both stubs are implemented. It only
// silences the "unused" warnings that `todo!()` bodies produce, so that
// `cargo clippy -- -D warnings` passes on the untouched scaffold.
#![allow(unused_variables, dead_code)]

use std::path::PathBuf;

use crate::DocumentChunk;

/// Chunk sizing parameters. Both are measured in Unicode scalar values
/// (`char`s), not bytes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ChunkConfig {
    /// Upper bound on the length of any chunk's `text`.
    pub max_chars: usize,
    /// Overlap parameter of specification section 8.
    pub overlap: usize,
}

impl ChunkConfig {
    /// Validate the parameters. `lib.rs` maps the `Err` string to a Python
    /// `ValueError`, so the message is user-facing.
    pub fn new(max_chars: usize, overlap: usize) -> Result<Self, String> {
        if max_chars == 0 {
            return Err("max_chars must be greater than 0".to_string());
        }
        if overlap >= max_chars {
            return Err(format!(
                "overlap ({overlap}) must be smaller than max_chars ({max_chars})"
            ));
        }
        Ok(Self { max_chars, overlap })
    }
}

/// Split one document into chunks.
///
/// `file_path` is copied into every chunk. `text` is the full file content.
///
/// Requirements C1 to C11: specification section 8.
pub fn chunk_document(file_path: &str, text: &str, cfg: &ChunkConfig) -> Vec<DocumentChunk> {
    todo!("chunk_document (IMPLEMENTATION_GUIDE.md section 8)")
}

/// Read and chunk many files.
///
/// Requirements F1 to F4: specification section 8.7. Runs with the GIL
/// released (see `chunk_directory` in `lib.rs`).
pub fn chunk_files(paths: &[PathBuf], cfg: &ChunkConfig) -> Vec<DocumentChunk> {
    todo!("chunk_files (IMPLEMENTATION_GUIDE.md section 8)")
}
