# Claude Code Todo: "Holy Grail" Timelapse Transition logic

## Objective
Upgrade `src/auto_timelapse.py` from a binary "Day/Night" switch to a smooth **Linear Ramping System**. This will eliminate the "flash" (exposure pop) and "color snap" (AWB shift) during sunset and sunrise.

---

## 1. Seamless Handover (The Flash Killer)
The "flash" occurs when the script switches from `AeEnable: True` to `Manual` with different settings.
* **Task:** The moment `current_lux` drops below the `day` threshold for the first time, the script must query the **camera metadata** of the *very last* frame.
* **Metadata to Capture:** `ExposureTime` and `AnalogueGain`.
* **The Seed:** Use these real-world values as the starting point for all interpolation math. This ensures Frame 1 of the transition is identical in brightness to the last frame of Auto mode.

## 2. Twilight Zone Interpolation (The Lerp)
Create a sliding scale between the two thresholds in `config.yml`.
* **Range:** Between `light_thresholds.day` (e.g., 100 lux) and `light_thresholds.night` (e.g., 10 lux).
* **Factor (t):** Calculate a progress value `t` where `0.0` is Day and `1.0` is Night.
* **Ramping:** Linearly interpolate the `ExposureTime` and `AnalogueGain` between the "Seed" values (from Task 1) and the `night_mode` targets defined in the config.
* **Manual Mode:** `AeEnable` must be `False` for the entire duration of the transition.

## 3. AWB Locking (The Color Stabilizer)
As light fades, Auto White Balance becomes unreliable and "flickers."
* **Task:** Once the transition begins (Lux < Day threshold), disable `AwbEnable`.
* **Fix Gains:** Capture the `AwbGains` (Red and Blue) from the last "Auto" frame metadata and apply them manually for every shot until the system returns to "Day" mode.

## 4. Implementation Details
* **Smoothness:** Ensure the `ema_lux` (Moving Average) is used for these calculations to prevent temporary shadows (like a bird or cloud) from triggering a transition.
* **Wait Time:** Ensure the loop accounts for longer shutter speeds (e.g., if shutter is 20s, the interval timer shouldn't start until the capture is finished).
* **Logging:** Update logs to show transition progress: 
  `[Transition] Progress: 45% | Shutter: 1.5s | Gain: 2.4 | AWB: Locked`

---

## How to execute:
1. Read `src/auto_timelapse.py` to understand the current `if/else` logic.
2. Read `src/capture_image.py` to see how it interacts with `Picamera2`.
3. Modify the capture loop to implement the ramping and handover logic described above.
