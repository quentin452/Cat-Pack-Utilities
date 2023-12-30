# File: fps_counter.py

import time

class FPSCounter:
    def __init__(self):
        self.start_time = time.time()
        self.frame_count = 0

    def count_frame(self):
        self.frame_count += 1

    def get_fps(self):
        current_time = time.time()
        elapsed_time = current_time - self.start_time
        if elapsed_time > 1:  # Calculate FPS every second
            fps = self.frame_count / elapsed_time
            self.frame_count = 0
            self.start_time = current_time
            return fps
        return None

    def update_fps_label(self, root, fps_label):
        fps = self.get_fps()
        if fps is not None:
            fps_label.config(text=f"FPS: {fps:.0f}")  # Update the label with FPS value
        root.after(1000, lambda: self.update_fps_label(root, fps_label))  # Update FPS label every second

    def update_fps(self, root):
        self.count_frame()  # Increment frame count for each frame processed
        root.after(8, lambda: self.update_fps(root))  # Update FPS count every 8 milliseconds
