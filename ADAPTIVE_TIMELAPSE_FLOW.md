# Adaptive Timelapse - How It Works

## Overview
The adaptive timelapse automatically adjusts camera settings based on ambient light conditions to capture properly exposed images 24/7 (day, night, dawn, dusk).

---

## Capture Flow (Per Frame)

### Step 1: Test Shot (Light Measurement)
**Purpose:** Quickly measure ambient light to determine day/night/transition mode

1. **Camera initialization:**
   - Opens NEW camera instance (separate from main timelapse camera)
   - Uses FIXED settings for consistent measurement:
     - Exposure: 0.1 seconds (100ms)
     - Gain: 1.0 (low ISO)
     - AWB: Enabled

2. **Capture:**
   - Saves to: `test_shots/test_YYYYMMDD_HHMMSS.jpg`
   - Uses `capture_request()` method to get image + metadata in ONE call
   - **Metadata IS saved:** `test_shots/test_YYYYMMDD_HHMMSS_metadata.json`

3. **Light analysis:**
   - Reads saved metadata
   - Calculates lux value using:
     - Image brightness (via PIL/numpy if available)
     - Exposure time from metadata
     - Analogue gain from metadata
   - Determines mode:
     - `lux < 10` → Night mode
     - `lux > 100` → Day mode
     - `10 ≤ lux ≤ 100` → Transition mode

4. **Camera cleanup:**
   - Camera closes automatically (uses context manager: `with ImageCapture(...)`)
   - **CRITICAL:** Camera MUST close before Step 2 to avoid "Camera in Running state" error

**Files created:**
- `test_shots/test_20251105_103045.jpg` (test image)
- `test_shots/test_20251105_103045_metadata.json` (test metadata)

---

### Step 2: Actual Frame Capture (Adaptive Settings)

1. **Camera initialization:**
   - Opens NEW camera instance (or reuses if mode unchanged)
   - Applies mode-specific settings:

   **Day Mode (lux > 100):**
   ```python
   AeEnable = True           # Auto exposure
   AwbEnable = True          # Auto white balance
   Brightness = 0.0          # No adjustment (or custom from config)
   # AnalogueGain = NOT SET  # Let auto-exposure decide
   # ExposureTime = NOT SET  # Let auto-exposure decide
   ```

   **Night Mode (lux < 10):**
   ```python
   AeEnable = False
   AwbEnable = False         # CRITICAL: AWB causes 5x slowdown!
   ExposureTime = 20s        # 20,000,000 microseconds
   AnalogueGain = 6.0        # High ISO for dark scenes
   ColourGains = [1.8, 1.5]  # Manual white balance
   FrameDurationLimits = (20.1s, 20.1s)  # CRITICAL for fast capture!
   ```

   **Transition Mode (10 ≤ lux ≤ 100):**
   ```python
   AeEnable = False
   AwbEnable = True
   ExposureTime = interpolated (50ms to 20s based on lux)
   AnalogueGain = interpolated (1.0 to 2.5 based on lux)
   ```

2. **Capture:**
   - Saves to: `test_photos/{project_name}_{timestamp}.jpg`
   - Uses `capture_request()` method - **ONE operation gets both image AND metadata**
   - **NO camera close/reopen** between image and metadata
   - **NO blocking delays** (unlike old `capture_metadata()` method)

3. **Metadata saved:**
   - Extracted from SAME request (line 370: `request.get_metadata()`)
   - Saves to: `test_photos/{project_name}_{timestamp}_metadata.json`
   - Contains:
     - Actual exposure time used
     - Actual analogue gain used
     - Color gains, temperature, lux
     - Timestamp, resolution, quality
   - **Non-blocking:** No 20+ second wait for next frame period

4. **Request cleanup:**
   - `request.release()` immediately frees buffer
   - Counter increments
   - Camera stays open (reused for next frame if mode unchanged)

**Files created:**
- `test_photos/raspilapse_2025-11-05T10:30:45.123456.jpg` (timelapse frame)
- `test_photos/raspilapse_2025-11-05T10:30:45.123456_metadata.json` (frame metadata)

---

### Step 3: Wait for Next Interval
- Calculates elapsed time
- Sleeps for `interval - elapsed` seconds
- Loop repeats from Step 1

---

## Performance Optimizations

### Long Exposure Optimization (Night Mode)
Without optimization, a 20-second exposure could take 99-124 seconds!

**Applied optimizations:**
1. ✅ `FrameDurationLimits` set to match exposure time (prevents pipeline delays)
2. ✅ `AwbEnable = 0` in night mode (AWB causes 5x slowdown!)
3. ✅ `buffer_count = 3` in configuration (prevents frame queuing)
4. ✅ `queue = False` (ensures fresh frame)
5. ✅ `capture_request()` instead of `capture_file()` + `capture_metadata()` (no blocking!)
6. ✅ Camera properly closed between test shot and actual capture

**Result:** 20-second exposure completes in ~20 seconds (not 99+ seconds) ⚡

---

## Metadata: YES, Saved After Every Shot

**Test shots:** ✅ Metadata saved to `test_shots/*_metadata.json`
**Actual frames:** ✅ Metadata saved to `test_photos/*_metadata.json`

**How it works:**
```python
# Single operation gets BOTH image and metadata
request = picam2.capture_request()
try:
    request.save("main", "image.jpg")           # Save image
    metadata = request.get_metadata()           # Get metadata (no blocking!)
    save_metadata_to_file(metadata)             # Save to JSON
finally:
    request.release()                           # Free buffer
```

**Key advantage:** No camera close/reopen needed. No blocking delays. Everything from ONE capture operation.

---

## Debugging: Test Shots Directory

**Purpose:** `test_shots/` contains diagnostic images used ONLY for light measurement

**Files stored:**
- Test images: `test_shots/test_YYYYMMDD_HHMMSS.jpg`
- Test metadata: `test_shots/test_YYYYMMDD_HHMMSS_metadata.json`

**These are NOT part of your timelapse** - they're just for calculating lux values.

**Cleanup:** You can delete `test_shots/` anytime (it will be recreated). Or add to `.gitignore`.

**Disable saving test shots?** Modify `auto_timelapse.py:297` to use a temporary file that gets deleted after analysis (future enhancement).

---

## Camera State Management

### CRITICAL: One Camera Instance at a Time

The Raspberry Pi camera hardware only allows **ONE active camera instance** at any time.

**How we handle this:**

1. **Test shot:** Uses context manager (`with ImageCapture(...)`) → auto-closes
2. **Wait for camera to fully close**
3. **Actual capture:** Opens new camera instance with adaptive settings
4. **Stays open:** Camera reused for next frame if mode unchanged
5. **Mode change?** Close and reinitialize with new settings

**Why we close between test shot and capture:**
- Test shot uses different settings (0.1s, gain 1.0)
- Actual capture uses adaptive settings (varies by mode)
- Trying to reconfigure without closing causes "Camera in Running state" error

---

## Configuration Controls

### Day Mode
```yaml
day_mode:
  awb_enable: true          # Auto white balance
  # brightness: 0.0         # Optional: -1.0 (darker) to 1.0 (brighter)
```
- Auto exposure handles everything
- Add `brightness` if images are too bright/dark

### Night Mode
```yaml
night_mode:
  max_exposure_time: 20.0   # Seconds (max before star trails)
  min_exposure_time: 1.0    # Seconds
  analogue_gain: 6          # ISO equivalent (1.0-8.0, recommend ≤4.0)
  awb_enable: false         # MUST be false for long exposures!
  colour_gains: [1.8, 1.5]  # Manual white balance (red, blue)
```

### Test Shot
```yaml
test_shot:
  enabled: true
  exposure_time: 0.1        # 100ms (fast measurement)
  analogue_gain: 1.0        # Low ISO
```

---

## Test Mode

Capture ONE image then exit:
```bash
python3 src/auto_timelapse.py --test
```

**Useful for:**
- Testing brightness settings
- Verifying camera works
- Quick exposure checks

**Output:**
- `test_shots/test_*.jpg` (light measurement)
- `test_photos/raspilapse_*.jpg` (actual frame with adaptive settings)

---

## Summary

| Aspect | Implementation |
|--------|---------------|
| **Metadata saved?** | ✅ YES - every shot (test + actual) |
| **Open/close for metadata?** | ❌ NO - single `capture_request()` call |
| **Test shots stored?** | ✅ YES - in `test_shots/` directory |
| **Blocking delays?** | ❌ NO - `capture_request()` is non-blocking |
| **Long exposure speed** | ⚡ 20s exposure = ~20s total (optimized!) |
| **Camera instances** | 🔄 Opens/closes between test shot and actual capture |
| **Filename pattern** | 📅 Timestamp or counter (configurable) |

**The system is fully optimized for 24/7 timelapse with adaptive exposure!** 🎉
