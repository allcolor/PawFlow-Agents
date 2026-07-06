# Contributing to PawFlow

Thank you for your interest in PawFlow! We welcome contributions of all kinds — bug fixes, new tasks/services, documentation improvements, and feature ideas.

## Getting Started

1. Fork the repository
2. Clone your fork: `git clone https://github.com/YOUR_USER/PawFlow-Agents.git`
3. Create a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```
4. Install dependencies:
   ```bash
   pip install -r requirements.txt
   pip install -e ".[dev]"
   ```
5. Run the test suite:
   ```bash
   pytest tests/
   ```

## Making Changes

1. Create a feature branch: `git checkout -b my-feature`
2. Make your changes
3. Ensure tests pass: `pytest tests/`
4. Push and open a PR against `main`

## Code Style

- Python 3.10+ (use type hints where the surrounding code does)
- No strict formatter enforced yet — be consistent with surrounding code
- Keep functions focused and files under ~500 lines when practical

## Running Tests

```bash
# All tests
pytest tests/

# Specific file
pytest tests/test_agent_loop.py

# With coverage
pytest tests/ --cov=core --cov=tasks --cov=engine
```

Some tests require optional dependencies (tree-sitter, sentence-transformers). These are automatically skipped if the dependency is not installed.

## Project Structure

| Directory | Purpose |
|-----------|--------|
| `core/` | Core abstractions (FlowFile, Task, Service, Tool Registry) |
| `engine/` | Flow executor, parser, debugger, triggers |
| `tasks/` | Task implementations (system, io, data, control, ai) |
| `services/` | Service implementations (LLM, auth, storage) |
| `api/` | FastAPI routes and middleware |
| `tools/` | Tool handlers for agent use |
| `tests/` | Test suite |

## Adding a New Task

1. Create your task class in the appropriate `tasks/` subdirectory
2. Inherit from `BaseTask` and implement `execute()`
3. Register it in the subdirectory's `__init__.py`
4. Add tests in `tests/`

See [docs/development.md](docs/development.md) for a detailed guide.

## Releasing

Releases are lightweight git tags named `1.0.0-beta.<N>` on `main`. The
version bump and changelog entry go in the **same commit**, *before* the tag,
so the tagged commit carries the correct version.

Checklist (replace `bN` / `beta.N` with the new number):

1. **Bump the package version** in `pyproject.toml`:
   - `pyproject.toml` → `version = "1.0.0bN"`
   - `core.__version__` is derived from `pyproject.toml` in source checkouts and
     package metadata in installed builds; do not hardcode it in `core/__init__.py`.
2. **Update release metadata** where applicable: `CHANGELOG.md`,
   `PROJECT_SUMMARY.md`, and website fallback version metadata.
3. **Update `CHANGELOG.md`**: add a `## [1.0.0-beta.N] — YYYY-MM-DD`
   section at the top (newest first), grouped into `Added` / `Fixed` /
   `Security`, summarizing the commits since the previous tag
   (`git log --format='%h %s' 1.0.0-beta.<N-1>..HEAD`).
4. **Commit** the bump and changelog together:
   `git commit -m "Release 1.0.0-beta.N"`.
5. **Verify**: `python cli.py --version` prints `1.0.0bN`, and the relevant
   test suite is green.
6. **Tag and push** the release commit:
   ```bash
   git push origin main
   git tag 1.0.0-beta.N
   git push origin 1.0.0-beta.N
   ```

Note: the version string is `1.0.0bN` (PEP 440, used in packaging) but the
tag is `1.0.0-beta.N` (SemVer pre-release).

## Reporting Issues

Please use [GitHub Issues](https://github.com/allcolor/PawFlow-Agents/issues). Include:
- Steps to reproduce
- Expected vs actual behavior
- PawFlow version (`python cli.py --version`) and Python version

## Security Vulnerabilities

Do **not** open a public issue for security vulnerabilities. See [SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
