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

You do not need a Raspberry Pi to run the test suite. `picamera2` is imported
lazily and stubbed in tests; only one test is hardware-gated.

## Before you commit

```bash
make format    # black
make lint      # ruff
make test      # pytest
```

or `make all`, which runs all three.

**Use the black version pinned in `requirements-dev.txt`.** Black's stable
style changes between yearly releases, so a newer black on your machine will
reformat files that CI then rejects. Installing the dev requirements gives you
the right one; the pre-commit hook uses whatever `black` is on your `PATH`, so
check it with `black --version` if CI disagrees with you.

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
| `src/` | Application code. Every module is importable both as `src.x` and as bare `x` — the systemd units run scripts directly, so `sys.path[0]` is `src/`. |
| `scripts/` | Installer and operator tools (shell + standalone Python) |
| `systemd/` | Unit templates (`*.in`, substituted by `scripts/install.sh`) |
| `config/` | `config.example.yml` is the documented schema; `config.yml` is gitignored |
| `tests/` | pytest suite, one module per `src/` module |
| `docs/` | User documentation |

Never commit `config/config.yml` — it holds API keys. `.gitignore` covers it,
but check `git status` before you push.

## Releasing

Maintainers only.

1. `src/__version__.py` is the single source of truth for the version.
   `pyproject.toml` reads it dynamically; update `CITATION.cff` by hand.
2. Add a `CHANGELOG.md` entry under a new `## [x.y.z]` heading.
   `tests/test_version.py` asserts these two agree.
3. `make all` must pass.
4. Commit, then tag: `git tag vX.Y.Z && git push --tags`.
5. Create the GitHub release from the tag, pasting the CHANGELOG section.

Semantic versioning: major for breaking changes, minor for features, patch
for fixes.

### CI secrets

`CODECOV_TOKEN` lives in **Settings → Secrets and variables → Actions**.
Get its value from the Codecov repository settings page — never paste a token
into a file in this repository, including documentation.

---

Thank you for contributing.
