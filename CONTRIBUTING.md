# Contributing to Daythread

Thanks for helping. This project values **small, auditable changes** over
feature breadth.

## Ground rules

1. **No unsolicited autonomous actions.** Any new tool must declare a risk tier
   and an undo recipe (or explicitly mark itself irreversible).
2. **No network in the core.** Connectors are separate, opt-in modules.
3. **Tests are required.** New behavior needs a unit test; bug fixes need a
   regression test.
4. **Keep the baseline dependency-free.** Standard library only for v0.x.
5. **Explain decisions.** Add an ADR for architecture changes.

## Development

```bash
git clone <repo>
cd daythread
python3 -m unittest discover -s tests -v
python3 -m daythread demo
```

## Commit style

- `core: ...` for runtime/memory/policy/ledger
- `cli: ...` for command surface
- `docs: ...` for documentation
- `test: ...` for tests only

Keep commits focused. Describe the safety impact when touching the approval
gate, ledger, or tools.

## Pull requests

- Link the issue or describe the user-visible behavior.
- State the risk tier for any new tool.
- Confirm `make test` passes.
- Do not include secrets, personal data, or generated databases.

## License

By contributing you agree your contribution is licensed under Apache-2.0.
