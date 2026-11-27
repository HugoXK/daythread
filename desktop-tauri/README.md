# Daythread Tauri shell

Cross-platform Tauri v2 shell that drives the local `dt` CLI.

```bash
python3 -m pip install -e ..
cd desktop-tauri/src-tauri
cargo build
```

For a bundled app, install the Tauri CLI and run `cargo tauri build` (or
`npx @tauri-apps/cli build`). Set `DAYTHREAD_BIN` to override the `dt` binary.

The macOS-native SwiftUI shell in `desktop/` is an alternative with no Rust
toolchain requirement.
