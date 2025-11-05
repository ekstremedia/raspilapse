# 🎨 Black Formatting Guide

## The Problem

Black formatting keeps failing in CI because code isn't formatted before committing.

## The Solution (3 Options)

### Option 1: Use Makefile (EASIEST!)

```bash
# Before every commit:
make format
make test
make all  # Does both format + test

# Then commit:
git add -u
git commit -m "your message"
git push
```

### Option 2: Run Black Manually

```bash
# Format all files
black src/ tests/ --line-length=100

# Verify it worked
black --check src/ tests/

# Run tests
python3 -m pytest tests/ -v

# Commit
git add -u
git commit -m "your message"
```

### Option 3: Pre-commit Hooks (AUTOMATIC!)

```bash
# One-time setup:
pip3 install pre-commit
pre-commit install

# Now Black runs automatically on every git commit!
# No need to remember to run it manually.
```

## Quick Reference

| Command | What it does |
|---------|--------------|
| `make format` | Format code with Black |
| `make check` | Check if formatted correctly |
| `make test` | Run all tests |
| `make all` | Format, check, and test |
| `black src/ tests/ --line-length=100` | Format manually |
| `black --check src/ tests/` | Check without modifying |

## Why This Matters

- ✅ Consistent code style across the project
- ✅ Easier code reviews
- ✅ CI pipeline passes automatically
- ✅ No more "would reformat X files" errors

## Remember

**Always format before committing:**

```bash
make format  # or: black src/ tests/ --line-length=100
```

This is the #1 reason CI fails! 💥

---

**TL;DR**: Run `make format` before every commit!
