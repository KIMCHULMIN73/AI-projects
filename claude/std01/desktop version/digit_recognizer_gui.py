"""
Handwritten digit recognizer GUI for Raspberry Pi OS.

Draw a digit (0-9) on the canvas with the mouse/touchscreen, then press
"Recognize" to classify it using a scikit-learn model trained on MNIST.
"""

import os
import tkinter as tk
from tkinter import messagebox

import joblib
import numpy as np
from PIL import Image, ImageDraw, ImageOps

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "digit_model.joblib")

CANVAS_SIZE = 280
BRUSH_RADIUS = 10


class DigitRecognizerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Handwritten Digit Recognizer")
        self.root.resizable(False, False)

        if not os.path.exists(MODEL_PATH):
            messagebox.showerror(
                "Model not found",
                f"Could not find {MODEL_PATH}.\nRun train_model.py first.",
            )
            root.destroy()
            return

        self.model = joblib.load(MODEL_PATH)

        # In-memory image mirrors what is drawn on the canvas, used for preprocessing.
        self.image = Image.new("L", (CANVAS_SIZE, CANVAS_SIZE), color=0)
        self.draw = ImageDraw.Draw(self.image)

        self._build_widgets()
        self.last_x, self.last_y = None, None

    def _build_widgets(self):
        title = tk.Label(
            self.root, text="Draw a digit (0-9)", font=("Helvetica", 16)
        )
        title.pack(pady=(10, 0))

        self.canvas = tk.Canvas(
            self.root, width=CANVAS_SIZE, height=CANVAS_SIZE, bg="black", cursor="cross"
        )
        self.canvas.pack(padx=10, pady=10)
        self.canvas.bind("<Button-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_paint)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)

        button_frame = tk.Frame(self.root)
        button_frame.pack(pady=(0, 10))

        recognize_btn = tk.Button(
            button_frame, text="Recognize", width=12, height=2,
            font=("Helvetica", 12), command=self.recognize, bg="#4CAF50", fg="white"
        )
        recognize_btn.grid(row=0, column=0, padx=5)

        clear_btn = tk.Button(
            button_frame, text="Clear", width=12, height=2,
            font=("Helvetica", 12), command=self.clear_canvas, bg="#f44336", fg="white"
        )
        clear_btn.grid(row=0, column=1, padx=5)

        self.result_label = tk.Label(
            self.root, text="Result: -", font=("Helvetica", 22, "bold")
        )
        self.result_label.pack(pady=(0, 5))

        self.confidence_label = tk.Label(
            self.root, text="Confidence: -", font=("Helvetica", 12)
        )
        self.confidence_label.pack(pady=(0, 10))

    def on_press(self, event):
        self.last_x, self.last_y = event.x, event.y

    def on_paint(self, event):
        x, y = event.x, event.y
        if self.last_x is not None:
            self.canvas.create_line(
                self.last_x, self.last_y, x, y,
                width=BRUSH_RADIUS * 2, fill="white",
                capstyle=tk.ROUND, smooth=True, joinstyle=tk.ROUND,
            )
            self.draw.line(
                [self.last_x, self.last_y, x, y],
                fill=255, width=BRUSH_RADIUS * 2,
                joint="curve",
            )
        self.last_x, self.last_y = x, y

    def on_release(self, _event):
        self.last_x, self.last_y = None, None

    def clear_canvas(self):
        self.canvas.delete("all")
        self.image = Image.new("L", (CANVAS_SIZE, CANVAS_SIZE), color=0)
        self.draw = ImageDraw.Draw(self.image)
        self.result_label.config(text="Result: -")
        self.confidence_label.config(text="Confidence: -")

    def preprocess(self):
        """Convert the drawn image into a 28x28 MNIST-style normalized vector."""
        bbox = self.image.getbbox()
        if bbox is None:
            return None

        cropped = self.image.crop(bbox)

        # Pad to a square so the digit isn't distorted when resized.
        w, h = cropped.size
        side = max(w, h)
        padded = Image.new("L", (side, side), color=0)
        padded.paste(cropped, ((side - w) // 2, (side - h) // 2))

        # Add margin similar to MNIST's centered digits, then resize to 28x28.
        margin = int(side * 0.2)
        canvas_padded = Image.new("L", (side + margin * 2, side + margin * 2), color=0)
        canvas_padded.paste(padded, (margin, margin))
        final = canvas_padded.resize((28, 28), Image.LANCZOS)

        arr = np.asarray(final, dtype=np.float32) / 255.0
        return arr.reshape(1, -1)

    def recognize(self):
        vector = self.preprocess()
        if vector is None:
            messagebox.showinfo("No input", "Please draw a digit first.")
            return

        prediction = self.model.predict(vector)[0]
        probabilities = self.model.predict_proba(vector)[0]
        confidence = probabilities[prediction] * 100

        self.result_label.config(text=f"Result: {prediction}")
        self.confidence_label.config(text=f"Confidence: {confidence:.1f}%")


def main():
    root = tk.Tk()
    DigitRecognizerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
