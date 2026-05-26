import cv2
import webbrowser
import tkinter as tk
from PIL import Image, ImageTk

root = tk.Tk()
screen_width = root.winfo_screenwidth()
screen_height = root.winfo_screenheight()
root.geometry(f"{screen_width}x{screen_height}+0+0")

vid = cv2.VideoCapture(0)
vid.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
vid.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

CAM_WIDTH = 320
CAM_HEIGHT = 180

label_widget = tk.Label(root)
label_widget.place(x=screen_width - CAM_WIDTH, y=0)

detector = cv2.QRCodeDetector()

def update():
    _, frame = vid.read()

    data, bbox, _ = detector.detectAndDecode(frame)
    if data:
        print("QR Code erkannt:", data)

    opencv_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA)
    captured_image = Image.fromarray(opencv_image)
    w, h = captured_image.size
    crop = 300
    captured_image = captured_image.crop((crop, 0, w - crop, h))
    cropped_w, cropped_h = captured_image.size
    new_h = int(CAM_WIDTH * cropped_h / cropped_w)
    captured_image = captured_image.resize((CAM_WIDTH, new_h))
    photo_image = ImageTk.PhotoImage(image=captured_image)
    label_widget.photo_image = photo_image
    label_widget.configure(image=photo_image)
    label_widget.after(5, update)

update()

root.bind('<Escape>', lambda e: root.quit())
root.mainloop()
