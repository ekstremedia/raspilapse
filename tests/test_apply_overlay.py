"""Tests for apply_overlay CLI script."""

import sys
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import tempfile
import shutil
import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from apply_overlay import main


@pytest.fixture
def temp_images():
    """Create temporary test images."""
    temp_dir = tempfile.mkdtemp()

    # Create fake images
    for i in range(3):
        img_path = Path(temp_dir) / f"test_{i}.jpg"
        img_path.write_text(f"fake image {i}")

    yield temp_dir

    # Cleanup
    shutil.rmtree(temp_dir)


def test_main_with_no_args():
    """Test main function with no arguments shows help."""
    with patch("sys.argv", ["apply_overlay.py"]):
        with pytest.raises(SystemExit):
            main()


def test_main_with_missing_image():
    """Test main with non-existent image."""
    with patch("sys.argv", ["apply_overlay.py", "/nonexistent/image.jpg"]):
        with patch("apply_overlay.logger") as mock_logger:
            result = main()
            assert result == 1  # Error exit code
            mock_logger.error.assert_called()


def test_main_with_single_image(temp_images):
    """Test processing a single image."""
    img_path = Path(temp_images) / "test_0.jpg"

    with patch("sys.argv", ["apply_overlay.py", str(img_path)]):
        with patch("apply_overlay.apply_overlay_to_image") as mock_apply:
            mock_apply.return_value = str(img_path)
            result = main()
            assert result == 0  # Success
            mock_apply.assert_called_once()


def test_main_with_multiple_images(temp_images):
    """Test processing multiple images."""
    img1 = Path(temp_images) / "test_0.jpg"
    img2 = Path(temp_images) / "test_1.jpg"

    with patch("sys.argv", ["apply_overlay.py", str(img1), str(img2)]):
        with patch("apply_overlay.apply_overlay_to_image") as mock_apply:
            mock_apply.return_value = "output.jpg"
            result = main()
            assert result == 0
            assert mock_apply.call_count == 2


def test_main_with_output_single_image(temp_images):
    """Test processing with custom output path."""
    img_path = Path(temp_images) / "test_0.jpg"
    output_path = Path(temp_images) / "output.jpg"

    with patch("sys.argv", ["apply_overlay.py", str(img_path), "-o", str(output_path)]):
        with patch("apply_overlay.apply_overlay_to_image") as mock_apply:
            mock_apply.return_value = str(output_path)
            result = main()
            assert result == 0


def test_main_with_output_multiple_images_error(temp_images):
    """Test error when using -o with multiple images."""
    img1 = Path(temp_images) / "test_0.jpg"
    img2 = Path(temp_images) / "test_1.jpg"

    with patch("sys.argv", ["apply_overlay.py", str(img1), str(img2), "-o", "output.jpg"]):
        with patch("apply_overlay.logger") as mock_logger:
            result = main()
            assert result == 1  # Error
            mock_logger.error.assert_called()


def test_main_with_output_dir(temp_images):
    """Test batch processing with output directory."""
    img1 = Path(temp_images) / "test_0.jpg"
    img2 = Path(temp_images) / "test_1.jpg"
    output_dir = Path(temp_images) / "output"

    with patch(
        "sys.argv",
        ["apply_overlay.py", str(img1), str(img2), "--output-dir", str(output_dir)],
    ):
        with patch("apply_overlay.apply_overlay_to_image") as mock_apply:
            mock_apply.return_value = "output.jpg"
            result = main()
            assert result == 0
            assert output_dir.exists()


def test_main_with_both_output_and_output_dir_error(temp_images):
    """Test error when both -o and --output-dir specified."""
    img_path = Path(temp_images) / "test_0.jpg"

    with patch(
        "sys.argv",
        [
            "apply_overlay.py",
            str(img_path),
            "-o",
            "out.jpg",
            "--output-dir",
            "outdir",
        ],
    ):
        with patch("apply_overlay.logger") as mock_logger:
            result = main()
            assert result == 1
            mock_logger.error.assert_called()


def test_main_with_metadata(temp_images):
    """Test processing with custom metadata file."""
    img_path = Path(temp_images) / "test_0.jpg"
    metadata_path = Path(temp_images) / "metadata.json"
    metadata_path.write_text('{"ExposureTime": 1000}')

    with patch("sys.argv", ["apply_overlay.py", str(img_path), "-m", str(metadata_path)]):
        with patch("apply_overlay.apply_overlay_to_image") as mock_apply:
            mock_apply.return_value = str(img_path)
            result = main()
            assert result == 0


def test_main_with_auto_metadata(temp_images):
    """Test processing with automatic metadata detection."""
    img_path = Path(temp_images) / "test_0.jpg"
    metadata_path = Path(temp_images) / "test_0_metadata.json"
    metadata_path.write_text('{"ExposureTime": 1000}')

    with patch("sys.argv", ["apply_overlay.py", str(img_path)]):
        with patch("apply_overlay.apply_overlay_to_image") as mock_apply:
            mock_apply.return_value = str(img_path)
            result = main()
            assert result == 0
            # Check that metadata path was detected
            call_args = mock_apply.call_args
            assert call_args[1]["metadata_path"] == str(metadata_path)


def test_main_without_metadata(temp_images):
    """Test processing without metadata file."""
    img_path = Path(temp_images) / "test_0.jpg"

    with patch("sys.argv", ["apply_overlay.py", str(img_path)]):
        with patch("apply_overlay.apply_overlay_to_image") as mock_apply:
            with patch("apply_overlay.logger") as mock_logger:
                mock_apply.return_value = str(img_path)
                result = main()
                assert result == 0
                # Should warn about missing metadata
                mock_logger.warning.assert_called()


def test_main_with_mode_override(temp_images):
    """Test processing with mode override."""
    img_path = Path(temp_images) / "test_0.jpg"

    with patch("sys.argv", ["apply_overlay.py", str(img_path), "--mode", "night"]):
        with patch("apply_overlay.apply_overlay_to_image") as mock_apply:
            mock_apply.return_value = str(img_path)
            result = main()
            assert result == 0
            # Check mode was passed
            call_args = mock_apply.call_args
            assert call_args[1]["mode"] == "night"


def test_main_with_custom_config(temp_images):
    """Test processing with custom config file."""
    img_path = Path(temp_images) / "test_0.jpg"
    config_path = "custom_config.yml"

    with patch("sys.argv", ["apply_overlay.py", str(img_path), "-c", config_path]):
        with patch("apply_overlay.apply_overlay_to_image") as mock_apply:
            mock_apply.return_value = str(img_path)
            result = main()
            assert result == 0
            # Check config was passed
            call_args = mock_apply.call_args
            assert call_args[1]["config_path"] == config_path


def test_main_with_verbose(temp_images):
    """Test processing with verbose logging."""
    img_path = Path(temp_images) / "test_0.jpg"

    with patch("sys.argv", ["apply_overlay.py", str(img_path), "-v"]):
        with patch("apply_overlay.apply_overlay_to_image") as mock_apply:
            with patch("apply_overlay.logger") as mock_logger:
                mock_apply.return_value = str(img_path)
                result = main()
                assert result == 0
                # Check logger.setLevel was called
                mock_logger.setLevel.assert_called_with("DEBUG")


def test_main_with_in_place_flag(temp_images):
    """Test processing with --in-place flag."""
    img_path = Path(temp_images) / "test_0.jpg"

    with patch("sys.argv", ["apply_overlay.py", str(img_path), "--in-place"]):
        with patch("apply_overlay.apply_overlay_to_image") as mock_apply:
            mock_apply.return_value = str(img_path)
            result = main()
            assert result == 0
            # Check output_path is None (in-place)
            call_args = mock_apply.call_args
            assert call_args[1]["output_path"] is None


def test_main_with_processing_error(temp_images):
    """Test handling of processing errors."""
    img_path = Path(temp_images) / "test_0.jpg"

    with patch("sys.argv", ["apply_overlay.py", str(img_path)]):
        with patch("apply_overlay.apply_overlay_to_image") as mock_apply:
            with patch("apply_overlay.logger") as mock_logger:
                mock_apply.side_effect = Exception("Processing failed")
                result = main()
                assert result == 1  # Error exit code
                mock_logger.error.assert_called()


def test_main_partial_success(temp_images):
    """Test processing with some successes and some failures."""
    img1 = Path(temp_images) / "test_0.jpg"
    img2 = Path(temp_images) / "test_1.jpg"
    img3 = Path(temp_images) / "test_2.jpg"

    with patch("sys.argv", ["apply_overlay.py", str(img1), str(img2), str(img3)]):
        with patch("apply_overlay.apply_overlay_to_image") as mock_apply:
            # First succeeds, second fails, third succeeds
            mock_apply.side_effect = [
                str(img1),
                Exception("Failed"),
                str(img3),
            ]
            result = main()
            assert result == 1  # Error because at least one failed


def test_main_summary_output(temp_images, capsys):
    """Test that summary is printed at the end."""
    img1 = Path(temp_images) / "test_0.jpg"
    img2 = Path(temp_images) / "test_1.jpg"

    with patch("sys.argv", ["apply_overlay.py", str(img1), str(img2)]):
        with patch("apply_overlay.apply_overlay_to_image") as mock_apply:
            with patch("apply_overlay.logger") as mock_logger:
                mock_apply.return_value = "output.jpg"
                main()
                # Check that summary info was logged
                info_calls = [call[0][0] for call in mock_logger.info.call_args_list]
                assert any("Processing Complete" in str(call) for call in info_calls)
                assert any("Total images" in str(call) for call in info_calls)


def test_main_can_be_called_as_script():
    """Test that script can be executed as __main__."""
    # Just verify the if __name__ == "__main__" block exists
    with open(Path(__file__).parent.parent / "src" / "apply_overlay.py") as f:
        content = f.read()
        assert 'if __name__ == "__main__":' in content
        assert "sys.exit(main())" in content
