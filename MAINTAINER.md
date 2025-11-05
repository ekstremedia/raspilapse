# Maintainer Guide

This document contains instructions for project maintainers and administrators.

## Table of Contents

- [Codecov Setup](#codecov-setup)
- [GitHub Secrets Management](#github-secrets-management)
- [Release Process](#release-process)

---

## Codecov Setup

Raspilapse uses [Codecov](https://codecov.io) for tracking test coverage across pull requests and commits.

### Initial Setup (Already Completed ✅)

The project is already configured with:
- ✅ Pytest with coverage support (`pytest-cov`)
- ✅ GitHub Actions workflow generates coverage reports
- ✅ Codecov action uploads reports automatically

### Adding the Codecov Token (Required for Maintainers)

**⚠️ Organization admin access required**

1. **Get the Codecov Token**

   The repository token for `ekstremedia/raspilapse` is:
   ```
   65ee3a9d-eb1c-4a27-993e-8b576f2d8cd7
   ```

   You can also find it at: https://app.codecov.io/github/ekstremedia/raspilapse/settings

2. **Add Token as GitHub Repository Secret**

   Navigate to:
   ```
   GitHub → ekstremedia/raspilapse → Settings → Secrets and variables → Actions
   ```

   Or use this direct link:
   https://github.com/ekstremedia/raspilapse/settings/secrets/actions

3. **Create New Repository Secret**

   - Click **"New repository secret"**
   - Name: `CODECOV_TOKEN`
   - Value: `65ee3a9d-eb1c-4a27-993e-8b576f2d8cd7`
   - Click **"Add secret"**

4. **Verify Setup**

   After adding the secret:
   - Push any commit to `main` or `develop` branch
   - Check GitHub Actions workflow runs successfully
   - Visit https://app.codecov.io/github/ekstremedia/raspilapse to see coverage dashboard

### How Coverage Works

**On Every Push/PR:**
1. GitHub Actions runs pytest with coverage enabled
2. Coverage report is generated as `coverage.xml`
3. Codecov action uploads the report
4. Codecov analyzes and displays results on PRs

**Coverage Report Features:**
- ✅ Line coverage (which lines are tested)
- ✅ Branch coverage (which code paths are tested)
- ✅ PR comments showing coverage changes
- ✅ Coverage trends over time
- ✅ Badges for README

### Codecov Badge

Add this to your README.md to show current coverage:

```markdown
[![codecov](https://codecov.io/gh/ekstremedia/raspilapse/branch/main/graph/badge.svg)](https://codecov.io/gh/ekstremedia/raspilapse)
```

**Note:** Badge is already present in README.md

---

## GitHub Secrets Management

### Current Secrets

The following secrets are configured for this repository:

| Secret Name | Purpose | Added By | Date |
|------------|---------|----------|------|
| `CODECOV_TOKEN` | Upload coverage reports | Maintainer | 2025-11-05 |

### Adding New Secrets

1. Navigate to: `Settings → Secrets and variables → Actions`
2. Click **"New repository secret"**
3. Enter name and value
4. Click **"Add secret"**

**Security Notes:**
- ⚠️ Never commit secrets to the repository
- ⚠️ Use repository secrets for public repos
- ⚠️ Use organization secrets for shared access across repos
- ✅ Secrets are encrypted and only exposed during workflow runs

---

## Release Process

### Version Numbering

Raspilapse follows [Semantic Versioning](https://semver.org/):
- **Major.Minor.Patch** (e.g., `1.0.0`)
- Major: Breaking changes
- Minor: New features (backward compatible)
- Patch: Bug fixes

**Current Version:** `0.9.0-beta`

### Creating a Release

1. **Update Version Numbers**
   - `README.md` - Update version badge
   - `CHANGELOG.md` - Document changes
   - Any version constants in code

2. **Run Full Test Suite**
   ```bash
   # Format code
   black src/ tests/ --line-length=100

   # Run tests with coverage
   pytest tests/ -v --cov=src --cov-branch --cov-report=term-missing

   # Verify formatting
   black --check src/ tests/
   ```

3. **Commit Version Changes**
   ```bash
   git add -u
   git commit -m "Release v0.9.0-beta"
   git push origin main
   ```

4. **Create GitHub Release**
   - Go to: https://github.com/ekstremedia/raspilapse/releases/new
   - Tag: `v0.9.0-beta`
   - Title: `v0.9.0-beta - Description`
   - Description: Copy from CHANGELOG.md
   - Click **"Publish release"**

5. **Verify Release**
   - Check GitHub Actions pass
   - Verify Codecov report is generated
   - Test installation from release tarball

---

## CI/CD Pipeline

### GitHub Actions Workflows

**`.github/workflows/tests.yml`**
- Runs on: Push to `main`/`develop`, PRs, manual trigger
- Tests: Python 3.9, 3.10, 3.11, 3.12
- Steps:
  1. Code linting (flake8)
  2. Format checking (black)
  3. Unit tests (pytest)
  4. Coverage upload (codecov)
  5. Type checking (mypy)
  6. Compatibility check

### Monitoring CI/CD

- **GitHub Actions**: https://github.com/ekstremedia/raspilapse/actions
- **Codecov Dashboard**: https://app.codecov.io/github/ekstremedia/raspilapse
- **Test Status Badge**: Shows in README.md

### Troubleshooting Failed CI

**Common failures:**

1. **Black formatting**
   ```bash
   # Fix locally
   black src/ tests/ --line-length=100
   git add -u && git commit --amend --no-edit && git push -f
   ```

2. **Test failures**
   ```bash
   # Run locally to debug
   pytest tests/ -v -x  # Stop at first failure
   ```

3. **Coverage upload**
   - Check `CODECOV_TOKEN` secret is set
   - Verify token is valid at Codecov dashboard
   - Check `coverage.xml` is generated

---

## Maintenance Tasks

### Regular Updates

- **Monthly:** Update dependencies (`pip list --outdated`)
- **Quarterly:** Review and update documentation
- **As needed:** Respond to issues and PRs

### Dependency Updates

```bash
# Check outdated packages
pip list --outdated

# Update requirements files
pip install --upgrade pytest pytest-cov black flake8
pip freeze > requirements-dev.txt
```

### Security

- Enable GitHub Dependabot alerts
- Review security advisories
- Update dependencies promptly

---

## Contact

**Maintainer:** Terje Nesthus
**Email:** terje@ekstremedia.no
**GitHub:** [@ekstremedia](https://github.com/ekstremedia)

---

**Last Updated:** 2025-11-05
