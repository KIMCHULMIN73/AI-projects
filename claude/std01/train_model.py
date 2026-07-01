"""
Train a handwritten digit classifier on the MNIST dataset and save it to disk.
Run this once before launching digit_recognizer_gui.py.
"""

import time
import joblib
import numpy as np
from sklearn.datasets import fetch_openml
from sklearn.model_selection import train_test_split
from sklearn.neural_network import MLPClassifier
from sklearn.metrics import accuracy_score

MODEL_PATH = "digit_model.joblib"


def main():
    print("Downloading / loading MNIST dataset (this may take a while on first run)...")
    mnist = fetch_openml("mnist_784", version=1, as_frame=False, parser="auto")
    X, y = mnist.data.astype(np.float32) / 255.0, mnist.target.astype(np.int64)

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=10000, random_state=42, stratify=y
    )

    print(f"Training on {len(X_train)} samples, testing on {len(X_test)} samples...")
    clf = MLPClassifier(
        hidden_layer_sizes=(256, 128),
        activation="relu",
        solver="adam",
        alpha=1e-4,
        batch_size=256,
        learning_rate_init=1e-3,
        max_iter=40,
        early_stopping=True,
        n_iter_no_change=5,
        random_state=42,
        verbose=True,
    )

    start = time.time()
    clf.fit(X_train, y_train)
    elapsed = time.time() - start
    print(f"Training finished in {elapsed:.1f} seconds")

    y_pred = clf.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    print(f"Test accuracy: {acc * 100:.2f}%")

    joblib.dump(clf, MODEL_PATH)
    print(f"Model saved to {MODEL_PATH}")


if __name__ == "__main__":
    main()
