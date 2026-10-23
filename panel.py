import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
from core import DEFAULTS

ROOT = Path(__file__).resolve().parent


class Panel:
    def __init__(self, root):
        self.root, self.process, self.lines = root, None, queue.Queue()
        root.title("DeepSeek 微信群助手")
        root.geometry("900x800")
        config = dict(DEFAULTS)
        if (ROOT / "config.json").exists():
            config.update(json.loads((ROOT / "config.json").read_text(encoding="utf-8")))
        self.config, self.fields = config, {}
        form = ttk.Frame(root, padding=16)
        form.pack(fill="x")
        ttk.Label(form, text="OCR 模式：微信与助手并排放置。先框选完整左右气泡区（排除头像），自动回复还需框选空白输入区。",
                  wraplength=790).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 14))
        labels = [("group", "完整群名 / 联系人备注名"), ("self_names", "自己的群昵称（私聊测试可留空）"),
                  ("api_key", "DeepSeek API Key（仅保存在本地）"), ("model", "模型"),
                  ("cooldown_seconds", "最短回复间隔 / 秒"), ("hourly_limit", "每小时回复上限"),
                  ("persona", "聊天风格")]
        for index, (key, label) in enumerate(labels, 1):
            ttk.Label(form, text=label).grid(row=index, column=0, sticky="w", pady=5)
            var = tk.StringVar(value=str(config[key]))
            self.fields[key] = var
            ttk.Entry(form, textvariable=var, width=65, show="*" if key == "api_key" else "").grid(
                row=index, column=1, sticky="ew", padx=(12, 0))
        self.mode = tk.StringVar(value="自动回复" if config["mode"] == "live" else "预览回复")
        ttk.Label(form, text="运行模式").grid(row=8, column=0, sticky="w", pady=6)
        ttk.Combobox(form, textvariable=self.mode, values=["预览回复", "自动回复"], state="readonly").grid(
            row=8, column=1, sticky="w", padx=(12, 0))
        self.kind = tk.StringVar(value="私聊测试" if config.get("chat_kind") == "private" else "群聊")
        ttk.Label(form, text="会话类型").grid(row=9, column=0, sticky="w", pady=6)
        ttk.Combobox(form, textvariable=self.kind, values=["私聊测试", "群聊"], state="readonly").grid(
            row=9, column=1, sticky="w", padx=(12, 0))
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="预览也会调用 DeepSeek 并产生 API 费用。昵称要与检测显示一致，避免机器人回应自己。",
                  wraplength=790).grid(row=10, column=0, columnspan=2, sticky="w", pady=10)
        controls = ttk.Frame(root, padding=(16, 0))
        controls.pack(fill="x")
        self.buttons = []
        for text, command in [("保存设置", self.save), ("框选气泡区", lambda: self.calibrate("messages")),
                              ("框选空白输入区", lambda: self.calibrate("input")), ("检测读取（30秒）", lambda: self.start(True)),
                              ("启动", lambda: self.start(False)), ("停止", self.stop)]:
            button = ttk.Button(controls, text=text, command=command)
            button.pack(side="left", padx=(0, 10))
            self.buttons.append(button)
        self.log = tk.Text(root, wrap="word", state="disabled", font=("Microsoft YaHei UI", 10))
        self.log.pack(fill="both", expand=True, padx=16, pady=16)
        self.status = tk.StringVar(value="未启动")
        ttk.Label(root,textvariable=self.status,padding=(16,0)).pack(fill="x")
        self.lines.put("准备就绪。首次使用请先做读取检测；检测不调用 API、不发送消息。")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(150, self.refresh)

    def save(self):
        for key, var in self.fields.items():
            value = var.get().strip()
            if key in {"cooldown_seconds", "hourly_limit"}:
                try:
                    value = int(value)
                    if value <= 0:
                        raise ValueError()
                except ValueError:
                    messagebox.showerror("设置错误", "回复间隔和每小时上限必须是正整数")
                    return False
            self.config[key] = value
        self.config["mode"] = "live" if self.mode.get() == "自动回复" else "preview"
        self.config["chat_kind"] = "private" if self.kind.get() == "私聊测试" else "group"
        self.config["backend"] = "ocr"
        (ROOT / "config.json").write_text(json.dumps(self.config, ensure_ascii=False, indent=2), encoding="utf-8")
        return True

    def calibrate(self, kind):
        if self.process and self.process.poll() is None:
            return
        if not self.save() or not self.config["group"]:
            messagebox.showinfo("先填写名称", "请先填写完整联系人备注名/群名")
            return
        self.root.withdraw()
        def launch():
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            process = subprocess.Popen([sys.executable, "-u", str(ROOT/"ocr_calibrate.py"),kind],
                cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", creationflags=subprocess.CREATE_NO_WINDOW)
            def poll():
                if process.poll() is None:
                    self.root.after(200,poll)
                else:
                    self.lines.put(process.stdout.read().strip() or "校准已取消。")
                    self.root.deiconify()
            poll()
        self.root.after(350, launch)

    def start(self, probe):
        if self.process and self.process.poll() is None:
            return
        if not self.save():
            return
        if not self.config["group"] or (self.config["chat_kind"] == "group" and not self.config["self_names"]):
            messagebox.showerror("设置未完成", "请填写完整会话名称；群聊还需要自己的群昵称")
            return
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        args = [sys.executable, "-u", str(ROOT / "runner.py")]
        if probe:
            args.append("--probe")
        self.process = subprocess.Popen(args, cwd=ROOT, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW)
        process = self.process
        def collect():
            for line in process.stdout:
                self.lines.put(line.rstrip())
            self.lines.put(f"程序结束（退出码 {process.wait()}）。")
        threading.Thread(target=collect, daemon=True).start()

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=5)
            self.lines.put("已停止。若刚好正在发送，请检查微信是否留下草稿或已发出消息。")

    def refresh(self):
        while not self.lines.empty():
            self.log.configure(state="normal")
            line = self.lines.get()
            self.log.insert("end", line + "\n")
            if line.startswith(("监听中","读取暂停","读取已恢复","自动发送失败","频率限制","已停止","程序结束","模式：","成功读取")):
                self.status.set(line[:120])
            if int(self.log.index("end-1c").split(".")[0]) > 500:
                self.log.delete("1.0", "100.0")
            self.log.see("end")
            self.log.configure(state="disabled")
        running = self.process and self.process.poll() is None
        for button in self.buttons[:-1]:
            button.configure(state="disabled" if running else "normal")
        self.root.after(150, self.refresh)

    def close(self):
        self.stop()
        self.root.destroy()


if __name__ == "__main__":
    # One panel per package directory, preventing two bots sending in parallel.
    import ctypes
    kernel = ctypes.windll.kernel32
    kernel.CreateMutexW.restype = ctypes.c_void_p
    import hashlib
    name = "Local\\WechatDeepseek_" + hashlib.sha256(str(ROOT).encode()).hexdigest()[:24]
    mutex = kernel.CreateMutexW(None, False, name)
    if not mutex or kernel.GetLastError() == 183:
        messagebox.showerror("已运行", "此目录的助手面板已经打开")
        sys.exit(1)
    root = tk.Tk()
    Panel(root)
    root.mainloop()
