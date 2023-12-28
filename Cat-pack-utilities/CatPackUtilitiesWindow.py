import tkinter as tk
from PIL import Image, ImageTk
import cProfile

original_image_size = None


def resize_background(event):
    global background_photo, background_image, background_canvas, original_image_size
    new_width = event.width
    new_height = event.height

    width_scale = new_width / original_image_size[0]
    height_scale = new_height / original_image_size[1]

    scale = min(width_scale, height_scale)

    resized_width = int(original_image_size[0] * scale)
    resized_height = int(original_image_size[1] * scale)

    resized_image = background_image.resize((resized_width, resized_height), Image.LANCZOS)

    black_background = Image.new('RGB', (new_width, new_height), color='black')
    black_background.paste(resized_image, ((new_width - resized_width) // 2, (new_height - resized_height) // 2))

    background_photo = ImageTk.PhotoImage(black_background)

    canvas.itemconfig(background_canvas, image=background_photo)



def main():
    global background_image, background_photo, canvas, background_canvas, original_image_size

    window = tk.Tk()
    window.title("Cat Pack Utilities V0.1")
    window.geometry("1280x720") # Set the default window size

    try:
        background_image = Image.open("test.png")
        original_image_size = background_image.size # Store the original size of the image
        background_photo = ImageTk.PhotoImage(background_image)

        # Set .png image as the window icon
        icon_image = Image.open("cat.png")
        window.iconphoto(False, ImageTk.PhotoImage(icon_image))

    except Exception as e:
        print(f"Failed to load images: {e}")
        return

    canvas = tk.Canvas(window)
    canvas.pack(fill=tk.BOTH, expand=True)

    background_canvas = canvas.create_image(0, 0, anchor=tk.NW, image=background_photo)

    window.bind("<Configure>", resize_background)

    # Profiling the application
    profiler = cProfile.Profile()
    profiler.enable()

    window.mainloop()

    profiler.disable()
    profiler.print_stats(sort='cumtime')

if __name__ == "__main__":
    main()