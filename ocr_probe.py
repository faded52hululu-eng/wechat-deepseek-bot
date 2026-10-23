"""User-controlled, read-only OCR test. No chat API key or sending code."""
import ctypes
import json
import queue
import subprocess
import tempfile
import threading
import time
import tkinter as tk
import re
import math
from tkinter import ttk, messagebox
from pathlib import Path
from PIL import ImageGrab, Image, ImageOps

ROOT = Path(__file__).resolve().parent


def map_ocr_box(box, angle, width, height, scale, border=24):
    # Windows OCR boxes use its deskewed image coordinates. Rotate the overlay
    # around the image center, as in Microsoft's OCR sample, before unscaling.
    theta = math.radians(angle or 0)
    cosine, sine = math.cos(theta), math.sin(theta)
    cx,cy = width/2,height/2
    left,top,right,bottom = box
    points = []
    for x,y in ((left,top),(right,top),(left,bottom),(right,bottom)):
        dx,dy = x-cx,y-cy
        points.append((cx+dx*cosine-dy*sine,cy+dx*sine+dy*cosine))
    return [(min(x for x,y in points)-border)/scale,
            (min(y for x,y in points)-border)/scale,
            (max(x for x,y in points)-border)/scale,
            (max(y for x,y in points)-border)/scale]


def _run_ocr(path):
    completed = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(ROOT / "ocr_file.ps1"), "-ImagePath", str(Path(path).resolve())],
        capture_output=True, encoding="utf-8", errors="replace", timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW)
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or "Windows OCR failed")
    return json.loads(completed.stdout.lstrip("\ufeff").strip())


def recognize_windows_file(path):
    with Image.open(path) as original:
        image = ImageOps.autocontrast(original.convert("RGB"))
        if image.convert("L").getextrema()[1] > 0:
            import numpy as np
            if float(np.median(np.asarray(image.convert("L")))) < 100:
                image = ImageOps.invert(image)
        scale = min(3.5, 2440 / max(image.size))
        if scale != 1:
            image = image.resize((int(image.width * scale), int(image.height * scale)), Image.Resampling.LANCZOS)
        image = ImageOps.expand(image, border=24, fill=image.getpixel((image.width-1,image.height-1)))
        with tempfile.TemporaryDirectory(prefix="wechat-ocr-scale-") as folder:
            prepared = Path(folder)/"prepared.png"
            image.save(prepared)
            result = _run_ocr(prepared)
    for line in result["lines"]:
        line["text"] = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", line["text"])
        line["box"] = map_ocr_box(line["box"],result.get("angle"),
            result.get("width",image.width),result.get("height",image.height),scale)
    return result


def recognize_file(path):
    from rapid_engine import recognize
    return recognize(path)


class Probe:
    def __init__(self, root):
        self.root, self.region, self.busy, self.continuous = root, None, False, False
        self.results = queue.Queue()
        self.previous = None
        self.closed = False
        root.title("微信 OCR 读取测试（不发消息）")
        root.geometry("820x620")
        ttk.Label(root, text="只测试屏幕文字读取，不调用 DeepSeek、不发送消息。\n"
            "先把微信聊天窗口放在屏幕上，再点“框选聊天区域”，只框消息区（不含输入框）。\n"
            "窗口被遮挡、移动或缩放后，需要重新框选。先保持浅色背景、较大字号。",
            padding=16, wraplength=780).pack(fill="x")
        bar = ttk.Frame(root, padding=(16, 0))
        bar.pack(fill="x")
        ttk.Button(bar, text="框选聊天区域", command=self.choose).pack(side="left", padx=(0, 8))
        ttk.Button(bar, text="识别一次", command=self.capture).pack(side="left", padx=(0, 8))
        ttk.Button(bar, text="连续检测（每3秒）", command=self.start).pack(side="left", padx=(0, 8))
        ttk.Button(bar, text="停止", command=self.stop).pack(side="left")
        self.status = tk.StringVar(value="尚未选择区域。框选后会自动识别一次。")
        ttk.Label(root, textvariable=self.status, padding=16).pack(fill="x")
        self.text = tk.Text(root, wrap="word", font=("Microsoft YaHei UI", 12))
        self.text.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.refresh)

    def choose(self):
        self.stop()
        if self.busy:
            return
        self.root.withdraw()
        self.root.after(400, self.overlay)

    def overlay(self):
        # This is an explicit user-operated selection, not an automatic window lookup.
        self.selection = tk.Toplevel(self.root)
        self.selection.overrideredirect(True)
        self.selection.attributes("-topmost", True)
        self.selection.attributes("-alpha", 0.30)
        user32 = ctypes.windll.user32
        self.origin_x, self.origin_y = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
        width, height = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
        self.selection.geometry(f"{width}x{height}{self.origin_x:+d}{self.origin_y:+d}")
        self.canvas = tk.Canvas(self.selection, bg="black", cursor="cross", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_text(30, 30, anchor="nw", fill="white", font=("Microsoft YaHei UI", 18),
            text="拖动框选微信消息区域；Esc 取消。不要包含输入框和本测试窗口。")
        self.canvas.bind("<ButtonPress-1>", self.begin)
        self.canvas.bind("<B1-Motion>", self.drag)
        self.canvas.bind("<ButtonRelease-1>", self.end)
        self.selection.bind("<Escape>", lambda _: self.cancel_selection())
        self.selection.focus_force()

    def begin(self, event):
        self.anchor = (event.x, event.y)
        self.rect = self.canvas.create_rectangle(event.x, event.y, event.x, event.y, outline="#00ff88", width=4)

    def drag(self, event):
        if hasattr(self, "rect"):
            self.canvas.coords(self.rect, *self.anchor, event.x, event.y)

    def end(self, event):
        if not hasattr(self, "anchor"):
            return
        ax, ay = self.anchor
        self.region = (self.origin_x + min(ax, event.x), self.origin_y + min(ay, event.y),
                       self.origin_x + max(ax, event.x), self.origin_y + max(ay, event.y))
        self.selection.destroy()
        if self.region[2] - self.region[0] < 60 or self.region[3] - self.region[1] < 40:
            self.region = None
            self.root.deiconify()
            self.status.set("区域太小，请重新框选。")
            return
        # Capture before restoring this panel, so the first test cannot read itself.
        self.root.after(300, lambda: self.capture(restore=True))

    def cancel_selection(self):
        self.selection.destroy()
        self.root.deiconify()

    def capture(self, restore=False):
        if self.busy:
            return
        if self.region is None:
            messagebox.showinfo("先框选", "请先框选微信消息区域")
            return
        image = ImageGrab.grab(bbox=self.region, all_screens=True)
        if restore:
            self.root.deiconify()
        self.busy = True
        self.status.set("正在本地识别……")
        def worker():
            try:
                # Temporary crops are deleted after OCR, not retained in logs.
                with tempfile.TemporaryDirectory(prefix="wechat-ocr-") as folder:
                    path = Path(folder)/"crop.png"
                    image.save(path)
                    result = recognize_file(path)
                self.results.put((result, None))
            except Exception as exc:
                self.results.put((None, str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def start(self):
        if self.region is None:
            self.choose()
            return
        self.continuous = True
        self.capture()

    def stop(self):
        self.continuous = False
        self.status.set("已停止连续检测。")

    def refresh(self):
        while not self.results.empty():
            result, error = self.results.get()
            self.busy = False
            if error:
                self.continuous = False
                self.status.set("识别失败")
                self.text.delete("1.0", "end")
                self.text.insert("end", error)
            else:
                content = "\n".join(line["text"] for line in result["lines"])
                changed = content != self.previous
                self.previous = content
                self.text.delete("1.0", "end")
                self.text.insert("end", content or "没有识别到文字，请检查框选范围。")
                self.status.set(f"{time.strftime('%H:%M:%S')} · {len(result['lines'])} 行 · "
                    + ("屏幕文字有变化" if changed else "文字未变化")
                    + " · 仅识别画面，尚未判断谁发言")
            if self.continuous:
                self.root.after(3000, self.scheduled_capture)
        if not self.closed:
            self.root.after(100, self.refresh)

    def scheduled_capture(self):
        if self.continuous and not self.closed:
            self.capture()

    def close(self):
        self.closed = True
        self.continuous = False
        self.root.destroy()


if __name__ == "__main__":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    root = tk.Tk()
    Probe(root)
    root.mainloop()
