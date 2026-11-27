# Daythread Tauri shell

Cross-platform Tauri v2 shell that drives the local `dt` CLI.

```bash
python3 -m pip install -e ..
cd desktop-tauri/src-tauri
cargo build
```

Bundle a `.app` and `.dmg`:

```bash
cd desktop-tauri
PATH="$HOME/.cargo/bin:$PATH" npx --yes @tauri-apps/cli@2 build
```

This produces `src-tauri/target/release/bundle/macos/Daythread.app` and
`bundle/dmg/Daythread_<version>_aarch64.dmg`. Set `DAYTHREAD_BIN` to override the
`dt` binary.

The macOS-native SwiftUI shell in `desktop/` is an alternative with no Rust
toolchain requirement.
