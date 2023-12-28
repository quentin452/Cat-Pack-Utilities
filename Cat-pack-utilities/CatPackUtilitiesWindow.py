import logging
import tkinter as tk
from PIL import Image, ImageTk
from functools import partial
from logging_config import configure_logging

original_image_size = None
resize_timer = None

# Configure logging
configure_logging()


def resize_background(event, window):
    global resize_timer
    if resize_timer:
        window.after_cancel(resize_timer)  # Cancel previous timer (if any)

    # Schedule a new timer to update the image after a delay (e.g., 100 milliseconds)
    resize_timer = window.after(100, partial(update_background, event, window))


def update_background(event, window):
    global background_photo, background_image, background_canvas, original_image_size
    new_width = event.width
    new_height = event.height

    # Create a copy of the original image to perform resizing operations
    resized_image = background_image.copy()

    # Calculate the scale
    width_scale = new_width / original_image_size[0]
    height_scale = new_height / original_image_size[1]
    scale = min(width_scale, height_scale)

    # Calculate the new size
    resized_width = int(original_image_size[0] * scale)
    resized_height = int(original_image_size[1] * scale)

    # Perform resizing using thumbnail method
    resized_image.thumbnail((resized_width, resized_height))

    # Create black background
    black_background = Image.new('RGB', (new_width, new_height), color='black')
    black_background.paste(resized_image, ((new_width - resized_width) // 2, (new_height - resized_height) // 2))

    background_photo = ImageTk.PhotoImage(black_background)
    canvas.itemconfig(background_canvas, image=background_photo)


def main():
    global background_image, background_photo, canvas, background_canvas, original_image_size

    logging.info("Launching the application.")

    window = tk.Tk()
    window.title("Cat Pack Utilities V0.1")
    window.geometry("1280x720")  # Set the default window size

    try:
        background_image = Image.open("test.png")
        original_image_size = background_image.size  # Store the original size of the image
        background_photo = ImageTk.PhotoImage(background_image)

        # Set .png image as the window icon
        icon_image = Image.open("cat.png")
        window.iconphoto(False, ImageTk.PhotoImage(icon_image))

    except Exception as e:
        logging.error(f"Failed to load images: {e}")
        return

    canvas = tk.Canvas(window)
    canvas.pack(fill=tk.BOTH, expand=True)

    background_canvas = canvas.create_image(0, 0, anchor=tk.NW, image=background_photo)

    window.bind("<Configure>", partial(resize_background, window=window))

    window.mainloop()


if __name__ == "__main__":
    main()
