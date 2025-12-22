import time
from PIL import Image, ImageDraw
from luma.led_matrix.device import max7219
from luma.core.interface.serial import spi, noop
from luma.core.render import canvas
from luma.core.legacy import text
from luma.core.legacy.font import proportional, CP437_FONT

# --- HARDWARE CONFIGURATION ---
# LED Matrix Dimmensions (overriding the config.ini)
MODULE_W = 24
MODULE_H = 2
MODULE_SIZE = 8
CANVAS_WIDTH = MODULE_W * MODULE_SIZE
CANVAS_HEIGHT = MODULE_H * MODULE_SIZE

# --- SCALING CONFIGURATION ---
BASE_SCALE_FACTOR_W = 1 
BASE_SCALE_FACTOR_H = 2 

BASE_WIDTH = CANVAS_WIDTH // BASE_SCALE_FACTOR_W
BASE_HEIGHT = CANVAS_HEIGHT // BASE_SCALE_FACTOR_H

BUFFER_SIZE = 1 #Buffer applied to the borders of the image

# --- INITIALIZATION ---
try:
    serial = spi(port=0, device=0, gpio=noop())
    device = max7219(
        serial, 
        width=CANVAS_WIDTH,
        height=CANVAS_HEIGHT, 
        block_orientation=-90, 
        rotate=0 
    )
    device.brightness = 0 # Default
    DEVICE_AVAILABLE = True
except Exception as e:
    print(f"[LED LIBRARY] Hardware initialization failed: {e}")
    DEVICE_AVAILABLE = False


def _process_snake_topology(img):
    """Internal function to handle 12x2 snake wiring corrections."""
    processed_img = Image.new('1', (CANVAS_WIDTH, CANVAS_HEIGHT), 0)
    
    for row_index in range(MODULE_H):
        y_start = row_index * MODULE_SIZE
        y_end = (row_index + 1) * MODULE_SIZE
        box = (0, y_start, CANVAS_WIDTH, y_end)
        
        row_img = img.crop(box)
 
        # Apply corrections based on verified LEDLargeTextTest.py snaking LED logic
        if row_index % 2 == 0:
            row_img = row_img.transpose(Image.FLIP_LEFT_RIGHT)
        else:
            row_img = row_img.transpose(Image.FLIP_TOP_BOTTOM)

        processed_img.paste(row_img, box)
        
    return processed_img


def scroll_message(message_text, scroll_delay=0.03):
    """
    NO LONGER SCROLLS as I was unable to scroll an image correctly.
    Public function to display a message on the LED matrix.
    Handles image generation, scaling, buffering, and topology correction.
    """
    if not DEVICE_AVAILABLE:
        print(f"[SIMULATION] Scrolling on LED: {message_text}")
        return

    # 1. Create Base Image
    base_img = Image.new('1', (BASE_WIDTH, BASE_HEIGHT), 0) 
    draw = ImageDraw.Draw(base_img)
    
    # 2. Draw text
    text(draw, (0, 0), message_text, fill="white", font=proportional(CP437_FONT))
                            
    # 3. Resize (Scaling)
    scaled_content = base_img.resize((CANVAS_WIDTH, CANVAS_HEIGHT), Image.Resampling.NEAREST)
            
    # 4. Apply Buffer
    final_canvas = Image.new('1', (CANVAS_WIDTH, CANVAS_HEIGHT), 0) 
    final_canvas.paste(scaled_content, (BUFFER_SIZE, BUFFER_SIZE))

    # 5. Apply Hardware Corrections (Vertical Flip + Snake)
    processed_image = final_canvas.transpose(Image.FLIP_TOP_BOTTOM)
    final_image = _process_snake_topology(processed_image)

    # 6. Display Frame
    device.display(final_image)
    time.sleep(scroll_delay)
