import tkinter as tk
from tkinter import font
from PIL import Image, ImageTk
from Catpackutilities.logging_config import configure_logging
from Catpackutilities.fps_counter import FPSCounter
from Catpackutilities.utilities import count_classes_in_jars
import subprocess
import os
import sys
import time
import platform

configure_logging()

root = None
canvas = None
background_photo = None
buttons = []
background_image = None
resize_timer = None
last_width = 0
last_height = 0


def adjust_buttons_position(new_width, new_height):
    """Adjust the position of all buttons"""
    global buttons
    if buttons:
        button_width = 200
        button_height = 100
        spacing = 20  # Spacing between buttons
        
        total_height = len(buttons) * button_height + (len(buttons) - 1) * spacing
        start_y = (new_height - total_height) / 2
        
        for i, button in enumerate(buttons):
            y_pos = start_y + i * (button_height + spacing)
            button.place(x=(new_width - button_width) / 2, y=y_pos)


def resize_background(event):
    global last_width, last_height
    current_width = root.winfo_width()
    current_height = root.winfo_height()

    if current_width != last_width or current_height != last_height:
        last_width = current_width
        last_height = current_height
        resize_image(current_width, current_height)
        adjust_buttons_position(current_width, current_height)


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


def run_word_name_searching():
    script_path = "Catpackutilities/utilities/word_or_name_searching.py"

    if platform.system() == 'Windows':
        # For Windows, use 'start' to open a new terminal window
        subprocess.Popen(["start", "cmd", "/k", sys.executable, script_path], shell=True)
    elif platform.system() in ['Linux', 'Darwin']:
        # For Unix-based systems (Linux, macOS), use different terminal emulators
        terminal_emulator = None
        if os.path.exists("/usr/bin/x-terminal-emulator"):
            terminal_emulator = "x-terminal-emulator"
        elif os.path.exists("/usr/bin/gnome-terminal"):
            terminal_emulator = "gnome-terminal"
        elif os.path.exists("/usr/bin/konsole"):  # Check for Konsole
            terminal_emulator = "konsole"
        else:
            print("No supported terminal emulator found.")
            return
        subprocess.Popen([terminal_emulator, "-e", sys.executable, script_path])
    change_button_text("Word Name Searching")


def run_duplicate_finder():
    script_path = "Catpackutilities/utilities/find_duplicate_mods.py"

    if platform.system() == 'Windows':
        # For Windows, use 'start' to open a new terminal window
        subprocess.Popen(["start", "cmd", "/k", sys.executable, script_path], shell=True)
    elif platform.system() in ['Linux', 'Darwin']:
        # For Unix-based systems (Linux, macOS), use different terminal emulators
        terminal_emulator = None
        if os.path.exists("/usr/bin/x-terminal-emulator"):
            terminal_emulator = "x-terminal-emulator"
        elif os.path.exists("/usr/bin/gnome-terminal"):
            terminal_emulator = "gnome-terminal"
        elif os.path.exists("/usr/bin/konsole"):  # Check for Konsole
            terminal_emulator = "konsole"
        else:
            print("No supported terminal emulator found.")
            return
        subprocess.Popen([terminal_emulator, "-e", sys.executable, script_path])
    change_button_text("Duplicate Mods Finder")

def run_class_count():
    """Lance le script de comptage de classes dans un terminal séparé"""
    script_path = "Catpackutilities/utilities/count_classes_in_jars.py" 

    if platform.system() == 'Windows':
        # Pour Windows, ouvrir un cmd séparé
        subprocess.Popen(["start", "cmd", "/k", sys.executable, script_path], shell=True)
    elif platform.system() in ['Linux', 'Darwin']:
        # Pour Linux/macOS, détecter un terminal disponible
        terminal_emulator = None
        if os.path.exists("/usr/bin/x-terminal-emulator"):
            terminal_emulator = "x-terminal-emulator"
        elif os.path.exists("/usr/bin/gnome-terminal"):
            terminal_emulator = "gnome-terminal"
        elif os.path.exists("/usr/bin/konsole"):
            terminal_emulator = "konsole"
        else:
            print("No supported terminal emulator found.")
            return
        subprocess.Popen([terminal_emulator, "-e", sys.executable, script_path])

    change_button_text("Count Classes in JARs")


def change_button_text(button_name):
    """Change le texte du bouton cliqué temporairement"""
    global buttons
    for button in buttons:
        if button["text"] == button_name:
            button["text"] = "Clicked!"
            root.after(3000, lambda btn=button, original_text=button_name: revert_button_text(btn, original_text))


def revert_button_text(button, original_text):
    """Remet le texte original du bouton"""
    button["text"] = original_text


def create_buttons_on_canvas():
    global buttons, root

    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()

    button_texture = Image.open("Catpackutilities/button1.png")
    button_texture = button_texture.resize((200, 100), Image.BILINEAR)
    button_texture = ImageTk.PhotoImage(button_texture)

    button_configs = [
        {
            "text": "Word Name Searching",
            "command": run_word_name_searching
        },
        {
            "text": "Duplicate Mods Finder", 
            "command": run_duplicate_finder
        },
        {
            "text": "Count Classes in JARs",
            "command": run_class_count
        }
    ]

    for i, config in enumerate(button_configs):
        button = tk.Button(
            root, 
            text=config["text"], 
            image=button_texture,
            compound=tk.CENTER, 
            command=config["command"],
            borderwidth=0, 
            relief="flat", 
            highlightthickness=0,
            activebackground=root.cget("bg"), 
            highlightbackground=root.cget("bg"),
            padx=0, 
            pady=0,
            font=font.Font(family="Arial", size=10, weight="bold")  
        )
        button.image = button_texture
        buttons.append(button)

    adjust_buttons_position(screen_width, screen_height)


def main():
    global root, canvas, background_photo, buttons, background_image, fps_label, last_width, last_height

    root = tk.Tk()
    root.title("Cat Pack Utilities V0.2") 

    desired_font = font.Font(family="Arial", size=12)
    for widget in [root] + root.winfo_children():
        widget.option_add("*Font", desired_font)

    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()

    if platform.system() == 'Windows':
        root.state('zoomed')  # Windows
    elif platform.system() in ['Linux', 'Darwin']:
        root.attributes('-zoomed', True)  # Linux et macOS

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

    version_label = tk.Label(root, text="v0.2 - Duplicate Finder Added", bg="black", fg="white")
    version_label.place(x=screen_width - 200, y=screen_height - 30)

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
    main()