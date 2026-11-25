//! Directory discovery. **Yours to implement.**
//!
//! Specification: `IMPLEMENTATION_GUIDE.md`, section 7.
//! Acceptance tests: `tests/test_rust_contract.py::TestWalkFiles`
//! (they skip themselves until the `todo!()` below is gone).

// TODO(you): delete this attribute once `walk_files` is implemented. It only
// silences the "unused variable" warnings that a `todo!()` body produces, so
// that `cargo clippy -- -D warnings` passes on the untouched scaffold.
#![allow(unused_variables)]

use std::io;
use std::path::{Path, PathBuf};

/// Collect the files under `root` selected by `extensions`.
///
/// Preconditions, established by `lib.rs`: `root` exists and is a directory;
/// `extensions` is non-empty and every entry is lowercase ASCII without a
/// leading dot.
///
/// Requirements W1 to W7: specification section 7. Runs with the GIL
/// released; it must not touch any Python object.
pub fn walk_files(root: &Path, extensions: &[String]) -> io::Result<Vec<PathBuf>> {
    todo!("walk_files (IMPLEMENTATION_GUIDE.md section 7)")
}
