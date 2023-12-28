import tkinter as tk
from PIL import Image, ImageTk

# Taille originale de l'image
original_image_size = None


def resize_background(event):
    global background_photo, background_image, background_canvas, original_image_size
    # Obtention de la taille de la fenêtre
    new_width = event.width
    new_height = event.height

    # Calcul du scale pour redimensionner l'image de façon proportionnelle
    width_scale = new_width / original_image_size[0]
    height_scale = new_height / original_image_size[1]

    # Choix du scale le plus petit pour garder les bordures noires si nécessaire
    scale = min(width_scale, height_scale)

    # Redimensionnement de l'image avec le scale calculé
    resized_width = int(original_image_size[0] * scale)
    resized_height = int(original_image_size[1] * scale)

    resized_image = background_image.resize((resized_width, resized_height), Image.LANCZOS)

    # Création d'une image de fond noire de la taille de la fenêtre
    black_background = Image.new('RGB', (new_width, new_height), color='black')
    black_background.paste(resized_image, ((new_width - resized_width) // 2, (new_height - resized_height) // 2))

    # Création de la nouvelle ImageTk.PhotoImage pour la fenêtre redimensionnée
    background_photo = ImageTk.PhotoImage(black_background)

    # Mise à jour de l'image affichée dans le canvas
    canvas.itemconfig(background_canvas, image=background_photo)


def main():
    global background_image, background_photo, canvas, background_canvas, original_image_size

    # Création de la fenêtre Tkinter
    window = tk.Tk()
    window.title("Cat Pack Utilities V0.1")
    window.geometry("1280x720")  # Définition de la taille de fenêtre par défaut

    try:
        # Chargement de l'image PNG pour l'arrière-plan
        background_image = Image.open("test.png")
        original_image_size = background_image.size  # Stockage de la taille originale de l'image
        background_photo = ImageTk.PhotoImage(background_image)
    except Exception as e:
        print(f"Failed to load background image: {e}")
        return

    # Création d'un canvas pour afficher l'image en arrière-plan
    canvas = tk.Canvas(window)
    canvas.pack(fill=tk.BOTH, expand=True)

    # Création de l'image sur le canvas
    background_canvas = canvas.create_image(0, 0, anchor=tk.NW, image=background_photo)

    # Liaison de la fonction de redimensionnement à l'événement de changement de taille de fenêtre
    window.bind("<Configure>", resize_background)

    # Boucle principale
    window.mainloop()


if __name__ == "__main__":
    main()
