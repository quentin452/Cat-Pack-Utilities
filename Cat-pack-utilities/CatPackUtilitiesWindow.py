import tkinter as tk

from PIL import Image, ImageTk

from logging_config import configure_logging

configure_logging()

original_image_size = None
resize_timer = None
first_launch = True  # Flag to indicate the first launch
canvas_frame = None  # Global canvas_frame variable


def create_buttons_on_canvas(canvas):
    for i in range(1, 11):
        button = tk.Button(canvas, text=f"Button {i}")
        canvas.create_window(50, 30 * i, anchor=tk.NW, window=button)


def resize_background(event, window):
    global resize_timer
    if resize_timer:
        window.after_cancel(resize_timer)  # Cancel previous timer (if any)

    resize_timer = window.after(100, update_background, event, window)


def update_background(event, window):
    global background_photo, background_image, background_canvas, original_image_size
    new_width = event.width
    new_height = event.height

    resized_image = background_image.resize((new_width, new_height), Image.BILINEAR)
    background_photo = ImageTk.PhotoImage(resized_image)
    canvas.itemconfig(background_canvas, image=background_photo)
    original_image_size = resized_image.size


def main():
    global background_image, background_photo, canvas, background_canvas, original_image_size

    window = tk.Tk()
    window.title("Cat Pack Utilities V0.1")
    window.geometry("1280x720")  # Set the default window size

    canvas = tk.Canvas(window)
    canvas.pack(fill=tk.BOTH, expand=True)

    background_image = Image.open("test.png")
    original_image_size = background_image.size
    background_photo = ImageTk.PhotoImage(background_image)

    icon_image = Image.open("cat.png")
    window.iconphoto(False, ImageTk.PhotoImage(icon_image))

    background_canvas = canvas.create_image(0, 0, anchor=tk.NW, image=background_photo)

    create_buttons_on_canvas(canvas)

    window.bind("<Configure>")

    window.mainloop()


if __name__ == "__main__":
    main()
