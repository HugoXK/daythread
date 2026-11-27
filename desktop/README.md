# Daythread macOS shell

A native SwiftUI shell that drives the local `dt` CLI. It is intentionally thin:
the agent, memory, approvals, and audit stay in the Python core.

## Build

```bash
# Make `dt` available to the app
python3 -m pip install -e ..

make app
open dist/Daythread.app
```

The app calls `dt` from `PATH`. To point it elsewhere:

```bash
DAYTHREAD_BIN=/usr/local/bin/dt open dist/Daythread.app
# or run the binary from a terminal
DAYTHREAD_BIN="$(which python3)" ./dist/Daythread.app/Contents/MacOS/Daythread
```

If `DAYTHREAD_BIN` points at a Python interpreter, the app runs
`python -m daythread`. `DAYTHREAD_HOME` is inherited from the environment.

## Features

- Today brief
- Run list with Approve / Reject / Undo
- Memory search

This shell is macOS-only. A cross-platform Tauri shell can reuse the same CLI
contract; the backend does not need to change.
