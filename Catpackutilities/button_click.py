import tkinter as tk


def button_click():
    label.config(text="Button Clicked")


def create_buttons():
    # Creating the main window
    root = tk.Tk()
    root.title("Clickable Buttons")

    # Creating a label
    global label
    label = tk.Label(root, text="Press a button")
    label.pack()

    # Creating buttons
    button1 = tk.Button(root, text="Button 1", command=button_click)
    button1.pack()

    button2 = tk.Button(root, text="Button 2", command=button_click)
    button2.pack()

    button3 = tk.Button(root, text="Button 3", command=button_click)
    button3.pack()

    # Run the Tkinter main loop
    root.mainloop()


if __name__ == "__main__":
    create_buttons()
