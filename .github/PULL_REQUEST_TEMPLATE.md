## Description

<!-- Briefly describe your changes -->

## Type of Change

- [ ] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to not work as expected)
- [ ] Documentation update
- [ ] Code refactoring
- [ ] Performance improvement

## Checklist

**BEFORE SUBMITTING, VERIFY:**

- [ ] ✅ **Code formatted with Black** (`make format` or `black raspilapse/ tests/ --line-length=100`)
- [ ] ✅ **All tests pass** (`make test` or `python3 -m pytest tests/ -v`)
- [ ] ✅ **Black check passes** (`make check` or `black --check raspilapse/ tests/`)
- [ ] 📝 Code has docstrings and comments where needed
- [ ] 🧪 Added tests for new features (if applicable)
- [ ] 📖 Updated documentation (if needed)
- [ ] 🔗 Linked to relevant issue(s)

## Testing

<!-- Describe how you tested your changes -->

- [ ] Tested locally on Raspberry Pi
- [ ] All unit tests pass
- [ ] Manual testing performed

## Additional Notes

<!-- Any additional information, screenshots, or context -->

---

**Did you format your code with Black?** ← Most common CI failure!

```bash
make format  # or: black raspilapse/ tests/ --line-length=100
```
