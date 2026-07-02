import tkinter as tk

root = tk.Tk()
root.title("Event Test")
c = tk.Canvas(root, width=300, height=300, bg="black")
c.pack()


def log(name):
    def handler(e):
        print(name, e.x, e.y, getattr(e, "state", None), flush=True)
    return handler


c.bind("<Button-1>", log("press"))
c.bind("<B1-Motion>", log("motion-b1"))
c.bind("<Motion>", log("motion"))
c.bind("<ButtonRelease-1>", log("release"))
root.mainloop()
