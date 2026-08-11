# Contributing to Raspilapse

Thanks for your interest in the project. Bug reports, config examples from
other latitudes, and pull requests are all welcome.

## Development setup

```bash
git clone https://github.com/ekstremedia/raspilapse.git
cd raspilapse
pip3 install -r requirements-dev.txt

# Optional but recommended: auto-formats on commit
pip3 install pre-commit && pre-commit install
```

On a PEP 668 system (Debian Bookworm and later, macOS Homebrew) those `pip3`
lines need `--break-system-packages` — or a virtualenv, which is fine on a
development machine, unlike on the camera.

You do not need a Raspberry Pi to run the test suite. `picamera2` is imported
lazily and stubbed in tests; only one test is hardware-gated.

## Before you commit

```bash
make format    # black
make lint      # ruff
make check     # black --check
make test      # pytest
```

or `make all`, which runs all four.

**Use the black version pinned in `requirements-dev.txt`.** Black's stable
style changes between yearly releases, so a newer black on your machine will
reformat files that CI then rejects. The pre-commit hook builds its own
isolated black from the `rev:` in `.pre-commit-config.yaml` — it never uses
the one on your `PATH` — so keep that rev, `requirements-dev.txt` and
`pyproject.toml` in step; a mismatch there is what makes CI disagree with a
clean local run.

## Pull requests

1. Branch from `main` (`git checkout -b feature/thing`).
2. Make the change, with tests.
3. Run `make all`.
4. Open the PR. Explain what problem it solves, not just what it changes.

Code standards: docstrings on public functions, tests for new behaviour,
line length 100 (enforced by black and ruff via `pyproject.toml`).

## Project layout

| Path | Contents |
|------|----------|
| `raspilapse/` | Application code, grouped by what it talks to: `camera/`, `overlay/`, `video/`, `storage/`, `cli/`. One import path per module — the units run `python3 -m raspilapse.cli.x` from the project directory. |
| `scripts/` | Installer and operator tools (shell + standalone Python) |
| `systemd/` | Unit templates (`*.in`, substituted by `scripts/install.sh`) |
| `config/` | `config.example.yml` is a short starter file; `docs/CONFIG-REFERENCE.yml` is the full schema; `config.yml` is gitignored |
| `tests/` | pytest suite, one module per application module (`__version__.py` is covered by `test_version.py`) |
| `tests/replay/` | Recorded sunsets, and what the exposure code decided about them — 3.8 MB of JSON that [its README](tests/replay/README.md) explains. Read that before touching a golden file. |
| `docs/` | User documentation |

Never commit `config/config.yml` — it holds API keys. `.gitignore` covers it,
but check `git status` before you push.

## Releasing

Maintainers only.

1. `raspilapse/__version__.py` is the single source of truth for the version.
   `pyproject.toml` reads it dynamically; update `CITATION.cff` by hand.
2. Add a `CHANGELOG.md` entry under a new `## [x.y.z]` heading.
   `tests/test_version.py` asserts `__version__`, the top CHANGELOG heading
   and `CITATION.cff` all agree, so a forgotten bump fails CI rather than
   shipping.
3. `make all` must pass.
4. Commit, then tag: `git tag vX.Y.Z && git push --tags`.
5. Create the GitHub release from the tag, pasting the CHANGELOG section.

Semantic versioning: major for breaking changes, minor for features, patch
for fixes.

### CI secrets

`CODECOV_TOKEN` lives in **Settings → Secrets and variables → Actions**.
Get its value from the Codecov repository settings page — never paste a token
into a file in this repository, including documentation.
