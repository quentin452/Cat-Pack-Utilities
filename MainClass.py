import tkinter as tk
import ctypes
from ctypes import wintypes
from PIL import Image, ImageTk
from Catpackutilities.logging_config import configure_logging
import subprocess
import sys
import time 

configure_logging()

root = None
canvas = None
background_photo = None
button = None
background_image = None
resize_timer = None
last_width = 0
last_height = 0

def get_screen_resolution():
    user32 = ctypes.windll.user32
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def get_taskbar_height():
    hwnd = ctypes.windll.user32.FindWindowW("Shell_TrayWnd", None)
    rect = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.bottom - rect.top

def resize_background(event):
    global last_width, last_height
    if event.width != last_width or event.height != last_height:
        last_width = event.width
        last_height = event.height
        resize_image(event.width, event.height)
        adjust_button_position(event.width, event.height)

def resize_image(new_width, new_height):
    global background_photo, background_image, canvas

    min_width = 400
    min_height = 300

    new_width = max(new_width, min_width)
    new_height = max(new_height, min_height)

    resized_image = background_image.resize((new_width, new_height), Image.BILINEAR)
    background_photo = ImageTk.PhotoImage(resized_image)

    canvas.config(width=new_width, height=new_height)
    canvas.delete("bg_image")
    canvas.create_image(0, 0, anchor=tk.NW, image=background_photo, tags="bg_image")

def resize_background(event):
    global last_width, last_height
    if event.width != last_width or event.height != last_height:
        last_width = event.width
        last_height = event.height
        resize_image(event.width, event.height)
        # adjust_button_position(event.width, event.height)

def adjust_button_position(new_width, new_height):
    global button
    if button:
        button.place(x=(new_width - 200) / 2, y=(new_height - 100) / 2)

def run_word_name_searching():
    subprocess.Popen([sys.executable, "Catpackutilities/utilities/word_or_name_searching.py"], creationflags=subprocess.CREATE_NEW_CONSOLE)
    change_button_text()

def change_button_text():
    global button
    if button:
        if button["text"] == "Word Name Searching":
            button["text"] = "Clicked!"
            root.after(3000, revert_button_text)  # Change back after 3000 milliseconds (3 seconds)

def revert_button_text():
    global button
    if button:
        button["text"] = "Word Name Searching"

def create_buttons_on_canvas():
    global button

    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()

    button_texture = Image.open("Catpackutilities/button1.png")
    button_texture = button_texture.resize((200, 100), Image.BILINEAR)
    button_texture = ImageTk.PhotoImage(button_texture)

    button = tk.Button(root, text="Word Name Searching", image=button_texture, compound=tk.CENTER,
                       command=run_word_name_searching)
    button.image = button_texture
    button.place(x=(screen_width - 200) / 2, y=(screen_height - 100) / 2)

def main():
    global root, canvas, background_photo, button, background_image, fps_counter

    root = tk.Tk()
    root.title("Cat Pack Utilities V0.1")

    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()

    root.state('zoomed')

    canvas = tk.Canvas(root)
    canvas.pack(fill=tk.BOTH, expand=True)

    background_image = Image.open("Catpackutilities/test.png")
    background_photo = ImageTk.PhotoImage(background_image)

    canvas.create_image(0, 0, anchor=tk.NW, image=background_photo, tags="bg_image")

    create_buttons_on_canvas()

    root.bind("<Configure>", resize_background)

    root.mainloop()

if __name__ == "__main__":
    main()
