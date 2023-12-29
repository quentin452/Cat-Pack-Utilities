import tkinter as tk
from PIL import Image, ImageTk
from Catpackutilities.logging_config import configure_logging
from Catpackutilities.fps_counter import FPSCounter
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

def adjust_button_position(new_width, new_height):
    global button
    if button:
        button.place(x=(new_width - 200) / 2, y=(new_height - 100) / 2)

def resize_background(event):
    global last_width, last_height
    current_width = root.winfo_width()
    current_height = root.winfo_height()

    if current_width != last_width or current_height != last_height:
        last_width = current_width
        last_height = current_height
        resize_image(current_width, current_height)
        adjust_button_position(current_width, current_height)

def resize_image(new_width, new_height):
    global background_photo, background_image, canvas

    # Define your minimum ratio here (for example, 0.5 means half of the application size)
    min_ratio = 1

    # Application width and height
    application_width = root.winfo_screenwidth()
    application_height = root.winfo_screenheight()

    min_width = max(int(application_width * min_ratio), 200)  # Minimum width at least 200
    min_height = max(int(application_height * min_ratio), 200)  # Minimum height at least 200

    resized_image = background_image.resize((new_width, new_height), Image.BILINEAR)
    background_photo = ImageTk.PhotoImage(resized_image)

    canvas.config(width=new_width, height=new_height)
    canvas.delete("bg_image")
    canvas.create_image(0, 0, anchor=tk.NW, image=background_photo, tags="bg_image")


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
    global button, root

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
    global root, canvas, background_photo, button, background_image, fps_label, last_width, last_height

    root = tk.Tk()
    root.title("Cat Pack Utilities V0.1")

    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()

    root.state('zoomed')

    canvas = tk.Canvas(root)
    canvas.pack(fill=tk.BOTH, expand=True)

    # Replace the video capture with an image
    image_path = "Catpackutilities/test.png"  # Replace with your image path
    background_image = Image.open(image_path)
    background_photo = ImageTk.PhotoImage(background_image)

    canvas.create_image(0, 0, anchor=tk.NW, image=background_photo, tags="bg_image")

    create_buttons_on_canvas()
    start_time = time.time()
    frame_count = 0
    
    # Create and place a label to display FPS in top-left corner
    fps_label = tk.Label(root, text="", bg="black", fg="white")
    fps_label.place(x=10, y=10)
    
    fps_counter = FPSCounter()

    def update_fps():
        fps_counter.update_fps(root)
        fps_counter.update_fps_label(root, fps_label)
    
    root.after(8, update_fps)

    root.bind("<Configure>", resize_background)

    last_width = root.winfo_width()
    last_height = root.winfo_height()

    root.mainloop()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print("An error occurred:")
        traceback.print_exc()