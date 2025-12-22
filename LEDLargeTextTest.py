# matrix_test.py
# Displays a message scaled up to fill an entire pixel matrix.
# Uses a configurable SCALE_FACTOR to demonstrate the scaling necessity.

import time
from PIL import Image, ImageDraw, ImageFont
from luma.led_matrix.device import max7219
from luma.core.interface.serial import spi, noop
from luma.core.legacy import text
from luma.core.legacy.font import proportional, CP437_FONT

# --- 1. SCALABLE CONFIGURATION ---
MODULE_W = 24  # Modules Wide
MODULE_H = 2  # Modules High
MODULE_SIZE = 8 # Pixel size of one module (8x8)

# Calculated Dimensions (Final Display Size)
CANVAS_WIDTH = MODULE_W * MODULE_SIZE 
CANVAS_HEIGHT = MODULE_H * MODULE_SIZE 

# SCALING CONFIGURATION
# We define how much smaller the base drawing canvas should be.
BASE_SCALE_FACTOR_W = 1 
BASE_SCALE_FACTOR_H = 2 

BASE_WIDTH = CANVAS_WIDTH // BASE_SCALE_FACTOR_W
BASE_HEIGHT = CANVAS_HEIGHT // BASE_SCALE_FACTOR_H  

# Define the desired buffer size in pixels on the FINAL display
BUFFER_SIZE = 1 

# --- 2. INITIALIZATION ---
# Using SPI interface (port 0, device 0)
serial = spi(port=0, device=0, gpio=noop())

# Device initialization parameters confirmed to work with the software fix
device = max7219(
    serial, 
    width=CANVAS_WIDTH,
    height=CANVAS_HEIGHT, 
    block_orientation=-90, 
    rotate=0 
)
device.brightness = 1

# --- 3. THE RENDERER LOGIC (Corrected Row-by-Row Flip) ---
def process_snake_topology(img):
    """
    Applies the necessary horizontal flip to every module row to correct the 
    internal snake topology (winding direction) without re-inverting content.
    """
    processed_img = Image.new('1', (CANVAS_WIDTH, CANVAS_HEIGHT), 0)
    
    # Iterate through every row of modules (0, 1, 2, ..., MODULE_H-1)
    for row_index in range(MODULE_H):
        y_start = row_index * MODULE_SIZE
        y_end = (row_index + 1) * MODULE_SIZE
        box = (0, y_start, CANVAS_WIDTH, y_end)
        
        # 1. Crop the data intended for this row
        row_img = img.crop(box)
 
        # 2. Use FLIP_LEFT_RIGHT to correct the horizontal winding.
        if row_index % 2 == 0:
                row_img = row_img.transpose(Image.FLIP_LEFT_RIGHT)
        else:
                row_img = row_img.transpose(Image.FLIP_TOP_BOTTOM)

        # 3. Paste the processed data back into the final image
        processed_img.paste(row_img, box)
        
    return processed_img

# --- 4. MAIN EXECUTION ---
MESSAGE = "SKW4648  PHL-> ORD  2700FT"
#MESSAGE = "88/88/88 88:88p WWWWWW 888F"
print(f"MAX7219 Renderer Started: {MODULE_W}x{MODULE_H} Modules ({CANVAS_WIDTH}x{CANVAS_HEIGHT} px)")
print(f"Base Drawing Canvas: {BASE_WIDTH}x{BASE_HEIGHT} (Scale: {BASE_SCALE_FACTOR_W}x wide, {BASE_SCALE_FACTOR_H}x high)")

try:
    # --- 1. Create the small base image for the text (16x8) ---
    # We draw the text to fill the base image completely.
    base_img = Image.new('1', (BASE_WIDTH, BASE_HEIGHT), 0) 
    draw = ImageDraw.Draw(base_img)
    
    # Draw text without any base-image offset
    text(draw, (0, 0), MESSAGE, fill="white", font=proportional(CP437_FONT))
    
    # --- 2. Resize the base image to the full canvas size (MAGNIFICATION 32x24) ---
    scaled_content = base_img.resize((CANVAS_WIDTH, CANVAS_HEIGHT), Image.Resampling.NEAREST)
    print(f"Text '{MESSAGE}' scaled to {CANVAS_WIDTH}x{CANVAS_HEIGHT}.")

    # --- 3. Create the final image with the desired buffer ---
    final_canvas = Image.new('1', (CANVAS_WIDTH, CANVAS_HEIGHT), 0) 

    # We paste the scaled content onto the final canvas with BUFFER_SIZE
    paste_position = (BUFFER_SIZE, BUFFER_SIZE)
    final_canvas.paste(scaled_content, paste_position)
    print(f"Content pasted at position {paste_position} on final {CANVAS_WIDTH}x{CANVAS_HEIGHT} canvas.")
    print(f"Result: 1-pixel buffer on all four sides of the text.")

    # --- 4. Apply the global vertical flip correction (Corrects physical stacking) ---
    processed_image = final_canvas.transpose(Image.FLIP_TOP_BOTTOM)
    print("Applied global Image.FLIP_TOP_BOTTOM correction.")

    # --- 5. Apply the per-row snake topology fix (Corrects horizontal winding) ---
    final_image = process_snake_topology(processed_image)

    # --- 6. Display ---
    device.display(final_image)

    print("\nText Displayed.")
    print("Running for 15 seconds...")
    time.sleep(15)

except KeyboardInterrupt:
    print("\n[INTERRUPTED]")

except Exception as e:
    print(f"\n[ERROR] {e}")

finally:
    device.clear()
    print("Display cleared.")