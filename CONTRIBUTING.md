# Contributing to Raspilapse

Thank you for your interest in contributing to Raspilapse! This document provides guidelines and instructions for contributing.

## Code of Conduct

- Be respectful and inclusive
- Provide constructive feedback
- Focus on what's best for the community
- Help others learn and grow

## How to Contribute

### Reporting Bugs

If you find a bug, please create an issue with:

1. **Clear title** - Describe the problem concisely
2. **Steps to reproduce** - How to trigger the bug
3. **Expected behavior** - What should happen
4. **Actual behavior** - What actually happens
5. **Environment details**:
   - Raspberry Pi model
   - Camera module version
   - Raspberry Pi OS version
   - Python version
   - Raspilapse version

### Suggesting Features

Feature requests are welcome! Please include:

1. **Use case** - Why is this feature needed?
2. **Proposed solution** - How should it work?
3. **Alternatives** - What other approaches did you consider?
4. **Additional context** - Any other relevant information

### Pull Requests

#### Before You Start

1. Check existing issues and PRs to avoid duplicates
2. For major changes, open an issue first to discuss
3. Fork the repository and create a feature branch

#### Development Setup

```bash
# Clone your fork
git clone https://github.com/YOUR_USERNAME/raspilapse.git
cd raspilapse

# Install dependencies
sudo apt install -y python3-picamera2 python3-yaml python3-pytest

# Install optional dev tools
sudo apt install -y python3-flake8 python3-black
```

#### Making Changes

1. **Create a feature branch**
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Write code**
   - Follow existing code style
   - Add docstrings to functions/classes
   - Keep functions focused and small
   - Use meaningful variable names

3. **Write tests**
   - Add unit tests for new functionality
   - Use mocks for camera hardware
   - Ensure tests pass without hardware
   - Aim for good coverage

4. **Update documentation**
   - Update README.md if needed
   - Update USAGE.md for user-facing changes
   - Add/update docstrings
   - Update CLAUDE.md for technical changes

5. **Run tests**
   ```bash
   # Run all tests
   python3 -m pytest tests/ -v

   # Run specific test file
   python3 -m pytest tests/test_capture_image.py -v

   # Run with coverage
   python3 -m pytest tests/ --cov=src --cov-report=term-missing
   ```

6. **Check code quality**
   ```bash
   # Check code formatting (if black is installed)
   black --check src/ tests/

   # Format code
   black src/ tests/

   # Lint code (if flake8 is installed)
   flake8 src/ tests/ --max-line-length=100
   ```

#### Commit Guidelines

- Use clear, descriptive commit messages
- Start with a verb: "Add", "Fix", "Update", "Remove"
- Keep the first line under 72 characters
- Add details in the body if needed

**Good examples:**
```
Add support for custom white balance settings

Update logging to include camera initialization steps

Fix metadata filename pattern for timestamp format
```

**Bad examples:**
```
updated stuff
fix
changes
```

#### Submitting a Pull Request

1. **Push to your fork**
   ```bash
   git push origin feature/your-feature-name
   ```

2. **Create pull request**
   - Go to https://github.com/ekstremedia/raspilapse
   - Click "New Pull Request"
   - Select your branch
   - Fill in the template

3. **PR description should include**:
   - What changed and why
   - Related issue numbers (e.g., "Fixes #123")
   - Testing performed
   - Screenshots (if UI/visual changes)

4. **Wait for review**
   - Address feedback promptly
   - Keep the PR focused and small when possible
   - Be patient and respectful

## Coding Standards

### Python Style

- Follow PEP 8 style guide
- Use 4 spaces for indentation (no tabs)
- Maximum line length: 100 characters
- Use type hints where appropriate

### Documentation

- Add docstrings to all public functions/classes
- Use Google-style docstrings:

```python
def capture_image(output_path: str, quality: int = 95) -> str:
    """
    Capture a single image.

    Args:
        output_path: Path where image should be saved
        quality: JPEG quality (0-100, default 95)

    Returns:
        Path to the captured image

    Raises:
        RuntimeError: If camera is not initialized
    """
    pass
```

### Testing

- Write unit tests for all new functionality
- Use pytest fixtures for setup/teardown
- Mock hardware dependencies (picamera2)
- Test edge cases and error handling
- Keep tests independent and repeatable

**Example test:**

```python
def test_capture_creates_output_file(mock_camera, temp_dir):
    """Test that capture creates an output file."""
    config = CameraConfig('config/config.yml')
    capture = ImageCapture(config)
    capture.initialize_camera()

    image_path, _ = capture.capture()

    assert os.path.exists(image_path)
    assert image_path.endswith('.jpg')
```

### Logging

- Use the logging module (not print statements)
- Choose appropriate log levels:
  - DEBUG: Detailed diagnostic information
  - INFO: General informational messages
  - WARNING: Warning messages for unexpected situations
  - ERROR: Error messages for failures
  - CRITICAL: Critical errors that may cause shutdown

```python
logger.info("Starting camera initialization")
logger.debug(f"Resolution set to {width}x{height}")
logger.error(f"Failed to capture image: {error}")
```

## Testing Without Hardware

All tests should run without requiring actual Raspberry Pi camera hardware.

### Using Mocks

```python
from unittest.mock import Mock, patch, MagicMock

@pytest.fixture
def mock_picamera2():
    """Mock the picamera2 module."""
    mock_camera = MagicMock()
    mock_camera.capture_file.return_value = None

    with patch.dict('sys.modules', {'picamera2': mock_camera}):
        yield mock_camera

def test_with_mock(mock_picamera2):
    """Test using mocked camera."""
    # Your test code here
    pass
```

## Project Structure

```
raspilapse/
├── .github/
│   └── workflows/
│       └── tests.yml          # GitHub Actions CI/CD
├── config/
│   └── config.yml             # Default configuration
├── src/
│   ├── capture_image.py       # Image capture module
│   └── logging_config.py      # Logging configuration
├── tests/
│   ├── test_capture_image.py  # Capture tests
│   └── test_logging_config.py # Logging tests
├── CONTRIBUTING.md            # This file
├── INSTALL.md                 # Installation guide
├── USAGE.md                   # Usage guide
├── README.md                  # Project overview
└── pytest.ini                 # Pytest configuration
```

## Review Process

1. **Automated checks** - CI/CD must pass
   - All tests must pass
   - Code must pass linting
   - Coverage should not decrease

2. **Code review** - Maintainer will review:
   - Code quality and style
   - Test coverage
   - Documentation updates
   - Performance implications

3. **Feedback** - Address any requested changes

4. **Merge** - PR will be merged when approved

## Getting Help

- **Questions?** Open a discussion or issue
- **Stuck?** Check existing issues and documentation
- **Need support?** Review INSTALL.md and USAGE.md

## Recognition

Contributors will be recognized in:
- GitHub contributors list
- Release notes (for significant contributions)

## License

By contributing, you agree that your contributions will be licensed under the same license as the project (MIT License).

---

Thank you for contributing to Raspilapse! 🎉
