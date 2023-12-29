import tkinter as tk
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

class FPSCounter:
    def __init__(self):
        self.frames = 0
        self.start_time = time.time()

    def count_frame(self):
        self.frames += 1

    def get_fps(self):
        current_time = time.time()
        elapsed_time = current_time - self.start_time
        if elapsed_time > 1:  # Calculate FPS every second
            fps = self.frames / elapsed_time
            self.frames = 0
            self.start_time = current_time
            return fps
        return None

fps_counter = FPSCounter()  # Create an instance of the FPSCounter

def update_fps_label():
    global fps_label
    fps = fps_counter.get_fps()
    if fps is not None:
        fps_label.config(text=f"FPS: {fps:.2f}")  # Update the label with FPS value
    root.after(1000, update_fps_label)  # Update FPS label every second

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
    global root, canvas, background_photo, button, background_image, fps_label

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
    
    # Function to update FPS label every 8 milliseconds
    def update_fps():
        nonlocal frame_count, start_time
        end_time = time.time()
        delta_time = end_time - start_time

        if delta_time > 0:  # Calculate FPS if time has elapsed
            fps = frame_count / delta_time
            fps_label.config(text=f"FPS: {fps:.2f}")

            # Log FPS to console
            #print(f"FPS: {fps:.2f}")  # Print FPS value to console

            frame_count = 0  # Reset frame count
            start_time = time.time()  # Reset start time

        frame_count += 1  # Increment frame count for each frame processed

        root.after(8, update_fps)  # Update FPS label every 8 milliseconds

    # Create and place a label to display FPS in top-left corner
    fps_label = tk.Label(root, text="", bg="black", fg="white")
    fps_label.place(x=10, y=10)

    #root.bind("<Configure>", resize_background)
    update_fps()  # Start updating the FPS label

    root.mainloop()

if __name__ == "__main__":
    main()
