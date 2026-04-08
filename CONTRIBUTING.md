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

## Reporting Issues

Please use [GitHub Issues](https://github.com/allcolor/PawFlow-Agents/issues). Include:
- Steps to reproduce
- Expected vs actual behavior
- PawFlow version (`python cli.py --version`) and Python version

## Security Vulnerabilities

Do **not** open a public issue for security vulnerabilities. See [SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
