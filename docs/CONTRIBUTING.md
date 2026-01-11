# Contributing to Raspilapse

Thank you for your interest in contributing to Raspilapse.

## Code Formatting (CRITICAL!)

**BEFORE EVERY COMMIT, RUN:**

```bash
black src/ tests/ --line-length=100
```

This is the #1 reason CI fails! Always format your code before committing.

### Quick Pre-Commit Checklist

```bash
# 1. Format code (REQUIRED!)
black src/ tests/ --line-length=100

# 2. Verify formatting
black --check src/ tests/

# 3. Run tests
python3 -m pytest tests/ -v

# 4. Commit
git add -u
git commit -m "your message"
git push
```

## Development Setup

```bash
# Install dependencies
pip3 install -r requirements-dev.txt

# Optional: Install pre-commit hooks (auto-formats on commit)
pip3 install pre-commit
pre-commit install
```

## Pull Request Process

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. **Format with Black** (line-length=100)
5. Run tests (`python3 -m pytest tests/`)
6. Commit and push
7. Open a Pull Request

## Code Standards

- **Black formatting** (line-length=100)
- **Docstrings** for all public functions
- **Tests** for new features
- **Clear commit messages**

See full guidelines: [CONTRIBUTING.md full version coming soon]

---

**Thank you for contributing.**
