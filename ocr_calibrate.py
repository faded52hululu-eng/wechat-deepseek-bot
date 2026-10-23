"""User chooses a physical screen region bound to one exact WeChat window."""
import argparse
import ctypes
import json
from pathlib import Path
import tkinter as tk
import win32gui
import win32process
import psutil

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=["messages", "input"])
    args = parser.parse_args()
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
    config = json.loads((ROOT/"config.json").read_text(encoding="utf-8"))
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-alpha", .30)
    user32 = ctypes.windll.user32
    ox, oy = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
    width, height = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
    root.geometry(f"{width}x{height}{ox:+d}{oy:+d}")
    canvas = tk.Canvas(root, bg="black", cursor="cross", highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    description = ("框选完整气泡区：包含左/右气泡，排除两侧头像、标题和输入框" if args.kind == "messages"
                   else "框选空白输入框内部：排除工具栏、发送按钮和边框")
    canvas.create_text(30, 30, text=description + "；Esc 取消", anchor="nw", fill="white", font=("Microsoft YaHei UI", 18))
    selection = {}
    def begin(event):
        selection["start"] = (event.x, event.y)
        selection["rect"] = canvas.create_rectangle(event.x, event.y, event.x, event.y, outline="#00ff88", width=4)
    def drag(event):
        if "start" in selection:
            canvas.coords(selection["rect"], *selection["start"], event.x, event.y)
    def finish(event):
        if "start" not in selection:
            return
        x, y = selection["start"]
        region = (ox+min(x,event.x), oy+min(y,event.y), ox+max(x,event.x), oy+max(y,event.y))
        root.withdraw()
        root.update()
        try:
            center = ((region[0]+region[2])//2, (region[1]+region[3])//2)
            hwnd = win32gui.GetAncestor(win32gui.WindowFromPoint(center), 2)
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if psutil.Process(pid).name().lower() not in {"weixin.exe", "wechat.exe"} or win32gui.GetWindowText(hwnd) != config["group"]:
                raise RuntimeError("框选区域不属于配置中的微信会话，请把微信放到前面并核对联系人名称。")
            bounds = win32gui.GetWindowRect(hwnd)
            if not (bounds[0] <= region[0] < region[2] <= bounds[2] and bounds[1] <= region[1] < region[3] <= bounds[3]):
                raise RuntimeError("框选区域必须完全位于同一个微信独立窗口内")
            if region[2]-region[0] < 80 or region[3]-region[1] < 35:
                raise RuntimeError("框选区域过小")
            path = ROOT/"ocr_calibration.json"
            saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            if saved.get("title") != config["group"] or saved.get("hwnd") != hwnd:
                saved = {}
            saved.update(title=config["group"], hwnd=hwnd, size=[bounds[2]-bounds[0], bounds[3]-bounds[1]])
            saved[args.kind] = [region[0]-bounds[0],region[1]-bounds[1],region[2]-bounds[0],region[3]-bounds[1]]
            path.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
            print("已保存" + ("气泡区域" if args.kind == "messages" else "输入区域"), flush=True)
        except Exception as exc:
            print("校准失败：" + str(exc), flush=True)
        root.destroy()
    canvas.bind("<ButtonPress-1>", begin)
    canvas.bind("<B1-Motion>", drag)
    canvas.bind("<ButtonRelease-1>", finish)
    root.bind("<Escape>", lambda _: root.destroy())
    root.focus_force()
    root.mainloop()


if __name__ == "__main__":
    main()
