import tkinter as tk
import ctypes
from ctypes import wintypes
from PIL import Image, ImageTk
from logging_config import configure_logging

configure_logging()

original_image_size = None
resize_timer = None
first_launch = True  # Flag to indicate the first launch
canvas_frame = None  # Global canvas_frame variable


def get_screen_resolution():
    user32 = ctypes.windll.user32
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def get_taskbar_height():
    hwnd = ctypes.windll.user32.FindWindowW("Shell_TrayWnd", None)
    rect = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.bottom - rect.top


""""
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
"""


def create_buttons_on_canvas(canvas):
    screen_width, screen_height = get_screen_resolution()
    button_width = 100

    center_x = (screen_width - button_width) / 2
    center_y = (screen_height - 30) / 2

    button_texture = Image.open("button1.png")
    button_texture = button_texture.resize((200, 100), Image.BILINEAR)
    button_texture = ImageTk.PhotoImage(button_texture)

    button = tk.Button(canvas, text="Word Name Searching", image=button_texture, compound=tk.CENTER)
    button.image = button_texture
    button.place(x=center_x, y=center_y)


def main():
    global original_image_size
    window = tk.Tk()
    window.title("Cat Pack Utilities V0.1")

    screen_width, screen_height = get_screen_resolution()
    taskbar_height = get_taskbar_height()
    window_width = screen_width
    window_height = screen_height - taskbar_height  # Subtract taskbar height
    window.geometry(f"{window_width}x{window_height}+0+0")  # Fullscreen without taskbar

    window.state('zoomed')

    canvas = tk.Canvas(window)
    canvas.pack(fill=tk.BOTH, expand=True)

    background_image = Image.open("test.png")
    original_image_size = background_image.size
    background_photo = ImageTk.PhotoImage(background_image)

    icon_image = Image.open("cat.png")
    window.iconphoto(False, ImageTk.PhotoImage(icon_image))

    canvas.create_image(0, 0, anchor=tk.NW, image=background_photo)

    create_buttons_on_canvas(canvas)

    window.bind("<Configure>")
    """
    window.bind("<Configure>", lambda event: resize_background(event, window))
    """
    window.mainloop()


if __name__ == "__main__":
    main()
