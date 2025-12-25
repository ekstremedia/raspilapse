# Claude Code Todo: Implement Keogram Generator

## Objective
Create a new script `src/create_keogram.py` that generates a "Time-Slice" summary image (Keogram) from a folder of timelapse images.

## Context
A Keogram works by taking the **center vertical slit** (1 pixel wide) of every image taken during the day and stitching them together left-to-right. The result shows the passage of time (clouds, day/night transitions, aurora) in a single static image.

## 1. Create `src/create_keogram.py`
**Dependencies:** Use `PIL` (Pillow), which is already installed.
**Logic:**
1.  **Arguments:** Accept input directory (path to images) and output filename.
2.  **File Discovery:** Find all `.jpg` files in the directory and sort them by filename (timestamp).
3.  **Canvas Setup:**
    * Read the height of the first image (e.g., 1080px).
    * The width of the Keogram = Number of images found (1 pixel per image).
    * Create a new blank RGB image: `Image.new('RGB', (num_images, height))`.
4.  **Stitching Loop:**
    * Iterate through every image.
    * Load the image.
    * Crop the center column: `box = (width//2, 0, width//2 + 1, height)`.
    * Paste this strip into the Keogram canvas at the current x-coordinate.
    * *Optimization:* Close the image file immediately after processing to save RAM.
5.  **Saving:** Save the result as a high-quality JPEG (e.g., `keogram_2025-12-25.jpg`).

## 2. Integrate with `src/make_timelapse.py`
**Goal:** Automatically generate the Keogram whenever a daily video is made.
**Action:**
* Import the `create_keogram` function into `make_timelapse.py`.
* After the `ffmpeg` video generation is complete, call the Keogram function for that same folder.
* Save the Keogram in the same directory as the video (or a specific `keograms/` folder if preferred).

## 3. Resilience Handling
* **Resolution Changes:** If an image has a different height than the first one (rare, but possible if config changed), resize it to match the target height before extracting the strip.
* **Empty Folders:** Handle cases gracefully where the folder is empty.

## Execution Steps
1.  Write the script.
2.  Test it on an existing daily folder: `python3 src/create_keogram.py --dir /var/www/html/images/2025/12/24/`
3.  Add the call to the daily video generation workflow.
