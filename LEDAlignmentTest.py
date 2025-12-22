# matrix_test.py
# SCALABLE RENDERER PROTOTYPE (4 modules wide x 3 modules high)
# This script renders text to a virtual canvas and then post-processes the pixels
# to match a specific hardware topology (Bottom-Right Start -> Snaking Up).

import time
from PIL import Image, ImageDraw
from luma.led_matrix.device import max7219
from luma.core.interface.serial import spi, noop
from luma.core.legacy import text
from luma.core.legacy.font import proportional, CP437_FONT

# --- 1. SCALABLE CONFIGURATION ---
# Updated to 3 modules high (4x3 = 12 total modules)
MODULE_W = 24  # Modules Wide
MODULE_H = 2  # Modules High
MODULE_SIZE = 8 # Pixel size of one module (8x8)

# Calculated Dimensions
CANVAS_WIDTH = MODULE_W * MODULE_SIZE
CANVAS_HEIGHT = MODULE_H * MODULE_SIZE # Should now be 24 pixels high (3 * 8)

# --- 2. INITIALIZATION ---
serial = spi(port=0, device=0, gpio=noop())

# Initialize the device with standard settings. We handle the custom topology in software.
device = max7219(
    serial, 
    width=CANVAS_WIDTH,
    height=CANVAS_HEIGHT, 
    block_orientation=-90, 
    rotate=0 
)
device.brightness = 1

# --- 3. THE RENDERER LOGIC ---
def process_snake_topology(img):
    """
    Post-process the image to handle snake topology.

    Every even-numbered row (0, 2, 4, ...) is flipped 180 degrees.
    """
    
    processed_img = Image.new('1', (CANVAS_WIDTH, CANVAS_HEIGHT), 0)
    
    for row_index in range(MODULE_H):
        # Row bounds
        y_start = row_index * MODULE_SIZE
        y_end = (row_index + 1) * MODULE_SIZE
        box = (0, y_start, CANVAS_WIDTH, y_end)
        
        row_img = img.crop(box)

        # Flip every even row
        if row_index % 2 == 0:
            row_img = row_img.transpose(Image.ROTATE_180)

        processed_img.paste(row_img, box)
        
    return processed_img

# --- 4. MAIN EXECUTION ---
print(f"MAX7219 Renderer Started: {MODULE_W}x{MODULE_H} Modules ({CANVAS_WIDTH}x{CANVAS_HEIGHT} px)")
print("Topology Fix: Rotating only the Top Row (Index 0) 180 degrees.")

try:
    font = CP437_FONT 
    
    # 1. Create the canvas
    img = Image.new('1', (CANVAS_WIDTH, CANVAS_HEIGHT), 0) 
    draw = ImageDraw.Draw(img)
    
    # Draw 'v' centered in every module slot
    TEXT_TO_DRAW = "v"
    NUM_TO_DRAW = 0
    CENTER_X = 1
    CENTER_Y = 0
    
    for r in range(MODULE_H):
        for c in range(MODULE_W):
            x = (c * MODULE_SIZE) + CENTER_X
            y = (r * MODULE_SIZE) + CENTER_Y
            #text(draw, (x, y), TEXT_TO_DRAW, fill="white", font=proportional(font))
            text(draw, (x, y), str(NUM_TO_DRAW), fill="white", font=proportional(font))
            NUM_TO_DRAW = (NUM_TO_DRAW + 1) % 10

    # 2. Pass through the Renderer to fix hardware topology issues
    img = process_snake_topology(img)

    # 3. Display
    device.display(img)

    print("\nTest Pattern Sent.")
    print("You should see 'v' on all 12 modules, pointing down.")
    print("Running for 15 seconds...")
    time.sleep(15)

except KeyboardInterrupt:
    print("\n[INTERRUPTED]")

except Exception as e:
    print(f"\n[ERROR] {e}")

finally:
    device.clear()
    print("Display cleared.")