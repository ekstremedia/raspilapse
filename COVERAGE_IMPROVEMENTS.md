# Code Coverage Improvements

This document describes the test coverage improvements made to address codecov concerns.

## Summary

Added **27 new tests** to improve coverage across modified files:
- Total tests increased from 194 to 221
- All tests passing (221 passed, 1 skipped)

## New Test Files Created

### 1. `tests/test_overlay_simplified.py` (14 tests)

Tests the new simplified 2-line overlay structure:

**TestSimplifiedStructure (9 tests)**
- `test_top_bar_simplified_lines` - Tests top-bar mode with new line structure
- `test_empty_right_lines` - Tests handling of empty right-side content
- `test_corner_mode_simplified` - Tests corner positions with simplified structure
- `test_localized_datetime_in_line_2` - Tests date/time localization
- `test_unknown_variables_handling` - Tests handling of undefined template variables
- `test_all_four_lines_populated` - Tests with all four positions having content
- `test_gradient_bar_with_two_lines` - Tests gradient background for 2-line layout
- `test_weather_data_missing` - Tests fallback when weather data unavailable
- `test_line_1_right_with_pipe_separator` - Tests complex formatting with separators

**TestFontHandling (2 tests)**
- `test_font_size_calculation` - Tests font scaling with image resolution
- `test_default_font_fallback` - Tests fallback to default font

**TestEdgeCases (3 tests)**
- `test_very_long_line_content` - Tests handling of very long text
- `test_special_characters_in_templates` - Tests special characters in templates
- `test_missing_metadata_values` - Tests with missing metadata keys

### 2. `tests/test_make_timelapse_daily.py` (13 tests)

Tests the new daily timelapse generation features:

**TestDefaultTwentyFourHours (3 tests)**
- `test_no_args_defaults_to_24_hours` - Tests default 24-hour mode
- `test_parse_time_valid` - Tests time parsing with valid input
- `test_parse_time_invalid` - Tests time parsing error handling

**TestDailyNaming (2 tests)**
- `test_daily_video_naming` - Tests simplified daily video naming
- `test_custom_range_naming` - Tests custom time range naming

**TestOutputDirectory (2 tests)**
- `test_output_dir_override` - Tests --output-dir parameter
- `test_output_dir_creates_if_missing` - Tests directory creation

**TestErrorHandling (3 tests)**
- `test_missing_config_file` - Tests missing config handling
- `test_no_images_found` - Tests handling when no images found
- `test_video_creation_failure` - Tests ffmpeg failure handling

**TestImageFinding (2 tests)**
- `test_find_images_in_range_organized` - Tests finding images with date organization
- `test_find_images_in_range_flat` - Tests finding images in flat structure

**TestCameraNameUsage (1 test)**
- `test_uses_camera_name_from_overlay` - Tests camera name extraction from config

## Coverage Areas Improved

### src/overlay.py
New coverage for:
- Simplified line_1_left, line_1_right, line_2_left, line_2_right structure
- Empty string handling in line positions
- Localized datetime with new structure
- Unknown template variables
- Weather data fallback ("-" values)
- Gradient bar with 2-line layout
- Font size calculation and fallback
- Edge cases with long text and special characters

### src/make_timelapse.py
New coverage for:
- Default 24-hour mode when no --start/--end provided
- Daily video naming pattern (`{project}_daily_YYYY-MM-DD.mp4`)
- --output-dir parameter override
- Camera name extraction from overlay config
- Time parsing validation
- Error handling for missing config, no images, video creation failure
- Image finding with both organized and flat directory structures

### Test Updates
Updated existing tests in `tests/test_overlay.py` to use new simplified structure:
- Replaced `main`, `camera_settings`, `debug` sections with simple line positions
- Updated assertions to match new structure
- Fixed test configurations

## Running the Tests

To run all tests:
```bash
python3 -m pytest tests/ -v
```

To run new overlay tests:
```bash
python3 -m pytest tests/test_overlay_simplified.py -v
```

To run new timelapse tests:
```bash
python3 -m pytest tests/test_make_timelapse_daily.py -v
```

## Results

All tests passing:
- ✅ 221 tests passed
- ⏭️ 1 test skipped
- ⏱️ ~45 seconds total runtime

## Recommendations for Further Coverage

To achieve even higher coverage:

1. **src/weather.py** - Add tests for:
   - Network timeout scenarios
   - Partial JSON responses
   - Cache file corruption

2. **src/capture_image.py** - Add tests for:
   - Camera initialization failures
   - Hardware-specific error handling
   - Metadata extraction edge cases

3. **Integration tests** - Add end-to-end tests:
   - Full timelapse capture and video generation
   - Overlay application with real camera data
   - Service installation and timer activation