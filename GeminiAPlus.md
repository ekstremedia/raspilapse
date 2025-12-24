# Claude Code Todo: Polar + Cinematic Refinement

## Context
We are working on the `gemini` branch. You have already implemented basic linear interpolation and AWB locking. Now we need to upgrade this logic to handle **Polar Coordinates (68°N)** and **Sequential Ramping** for professional quality.

---

## 1. Add "Polar Awareness" (Location Support)
**Goal:** Prevent the script from staying in "Night Mode" during the bright twilight of Polar Winter or Midnight Sun.
**Task:**
1.  **Dependency:** Add `astral` to `requirements.txt`.
2.  **Config:** Update `config.yml` to accept a new section: `location: { latitude: 68.7, longitude: 15.4 }`.
3.  **Logic Update:**
    * Import `LocationInfo` and `sun` from `astral`.
    * In the main loop of `auto_timelapse.py`, calculate `sun_elevation`.
    * **The "Civil Day" Override:** Modify your existing state check. Even if `lux < day_threshold`, force the system into **Day Mode** (Auto Exposure) if `sun_elevation > -6.0` (Civil Twilight).
    * *Why:* This ensures we capture the beautiful blue/pink twilight colors using Auto-White-Balance instead of locking them into a grey "Night" mode.

## 2. Upgrade to "Sequential Ramping" (Noise Reduction)
**Goal:** Your current code ramps Shutter and Gain *simultaneously*. We want to prioritize Shutter Speed to keep ISO (Gain) low.
**Task:**
* Refine the `calculate_manual_settings` function:
    * **Phase 1 (Shutter Priority):** When the transition starts, ramp `ExposureTime` from the seeding value up to `night_mode.max_exposure_time`. Keep `AnalogueGain` **locked** at the seeding value (approx 1.0) until the shutter is maxed out.
    * **Phase 2 (Gain Priority):** Only *after* `ExposureTime` has reached the limit, start ramping `AnalogueGain` up to the `night_mode` target.

## 3. Add "EV Safety Clamp"
**Goal:** Guarantee the handover from Auto to Manual is mathematically invisible.
**Task:**
* In the main loop, right before switching `AeEnable` to `False`:
    1.  Calculate `Auto_EV = Last_Auto_Exposure * Last_Auto_Gain`.
    2.  Calculate your `Proposed_Manual_EV`.
    3.  **The Clamp:** If they differ by >5%, force-override the first manual frame's settings to match `Auto_EV` exactly. Log this action: `[Safety] Clamped exposure to match Auto EV`.

## 4. Add Transition Hysteresis (Cloud Protection)
**Goal:** Prevent passing clouds from triggering "Night Mode."
**Task:**
* Add a `sustained_low_light_frames` counter.
* Only enter the Night Transition if:
    * `lux` is below the threshold for **5 consecutive frames**.
    * **AND** `sun_elevation` is below -6.0 (unless it is Deep Winter where sun never rises).

## 5. Metadata & Analysis
**Task:**
* Log the Sun Elevation in the console: `[Status] Sun: -4.2° | Lux: 45 | Mode: Polar Day`.
* Update `src/analyze_timelapse.py` to plot `SunElevation` alongside Lux.

---
## Execution
1.  Install `astral`.
2.  Refine `src/auto_timelapse.py` with the new logic.
3.  Update `config.yml`.
