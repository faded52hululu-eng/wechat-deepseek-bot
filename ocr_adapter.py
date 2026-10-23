import json
import tempfile
import time
from pathlib import Path
from PIL import ImageGrab
from ocr_probe import recognize_file
from ocr_messages import bubble_boxes, messages_from_lines, visual_observations, VisualBubbleTracker
from runtime_health import ReadUnavailable
from input_guard import input_draft_text

ROOT = Path(__file__).resolve().parent


class OCRWeChatAdapter:
    def __init__(self, group):
        import win32gui
        self.group = group
        path = ROOT/"ocr_calibration.json"
        if not path.exists():
            raise RuntimeError("请先在面板点击“框选气泡区”完成校准")
        self.calibration = json.loads(path.read_text(encoding="utf-8"))
        if self.calibration.get("title") != group or "messages" not in self.calibration:
            raise RuntimeError("联系人已变化或尚未选择气泡区，请重新校准")
        self.hwnd = self.calibration["hwnd"]
        self.tracker = VisualBubbleTracker()
        self.tail_cache = {}
        self.health = {"bubbles":0,"texts":0}
        self.validate()

    def validate(self):
        import win32gui, win32process, psutil
        if not win32gui.IsWindow(self.hwnd) or win32gui.GetWindowText(self.hwnd) != self.group:
            raise RuntimeError("校准的微信窗口已关闭/名称变化，请重新框选")
        _, pid = win32process.GetWindowThreadProcessId(self.hwnd)
        if psutil.Process(pid).name().lower() not in {"weixin.exe", "wechat.exe"}:
            raise RuntimeError("校准窗口不属于微信")
        if win32gui.IsIconic(self.hwnd):
            raise ReadUnavailable("OCR 需要微信窗口保持展开")
        bounds = win32gui.GetWindowRect(self.hwnd)
        if [bounds[2]-bounds[0],bounds[3]-bounds[1]] != self.calibration["size"]:
            raise RuntimeError("微信窗口尺寸已变化，请重新框选气泡区与输入区")
        return bounds

    def region(self, kind):
        import win32gui
        bounds = self.validate()
        if kind not in self.calibration:
            raise RuntimeError("请先在面板框选空白输入区，再使用自动回复")
        a,b,c,d = self.calibration[kind]
        box = (a+bounds[0], b+bounds[1], c+bounds[0], d+bounds[1])
        # Refuse to read an overlaid panel as if it were a chat message.
        for x,y in ((box[0]+2,box[1]+2),(box[2]-2,box[1]+2),
                    (box[0]+2,box[3]-2),(box[2]-2,box[3]-2),
                    ((box[0]+box[2])//2,(box[1]+box[3])//2)):
            owner = win32gui.GetAncestor(win32gui.WindowFromPoint((x,y)),2)
            if owner != self.hwnd:
                raise ReadUnavailable("微信校准区域被其他窗口遮挡，请并排摆放助手与微信")
        return box

    def read_once(self):
        image = ImageGrab.grab(bbox=self.region("messages"), all_screens=True)
        boxes = bubble_boxes(image)
        if not boxes:
            raise ReadUnavailable("未找到可识别的文字气泡；请检查完整左右气泡区，排除头像与输入框")
        with tempfile.TemporaryDirectory(prefix="wechat-ocr-read-") as folder:
            path = Path(folder)/"chat.png"
            image.save(path)
            result = recognize_file(path)
        observations = visual_observations(image,boxes,result["lines"])
        # Whole-screen OCR can omit a short last bubble. Retry only the latest
        # missing bubble, and cache by image signature rather than every poll.
        tail = observations[-1] if observations else None
        if tail is not None and not tail["text"]:
            a,b,c,d = tail["box"]
            key = (tail["side"],tail["signature"],c-a,d-b)
            cached = self.tail_cache.get(key)
            if cached is None or (not cached[0] and time.time()-cached[1] >= 15):
                with tempfile.TemporaryDirectory(prefix="wechat-ocr-tail-") as folder:
                    crop_path = Path(folder)/"bubble.png"
                    image.crop(tail["box"]).save(crop_path)
                    retry = recognize_file(crop_path)
                recovered = "\n".join(line["text"] for line in retry["lines"]).strip()
                self.tail_cache[key] = (recovered,time.time())
                if len(self.tail_cache) > 100:
                    self.tail_cache.pop(next(iter(self.tail_cache)))
            tail["text"] = self.tail_cache[key][0]
        messages = [(item["side"],item["text"]) for item in observations if item["text"]]
        self.health = {"bubbles":len(observations),"texts":len(messages),
                       "tail_unread":bool(tail and not tail["text"])}
        if not messages:
            diagnostics = ROOT/"diagnostics"
            diagnostics.mkdir(exist_ok=True)
            image.save(diagnostics/"last_read_failure.png")
            (diagnostics/"last_read_failure.json").write_text(json.dumps(
                {"size":image.size,"bubbles":boxes,"ocr":result},ensure_ascii=False,indent=2),encoding="utf-8")
            raise ReadUnavailable("找到了气泡，但未读到气泡内文字，请检查框选范围与字号")
        return observations

    def snapshot(self):
        messages = self.read_once()
        rows = self.tracker.update(messages)
        if not rows:
            time.sleep(.3)
            rows = self.tracker.update(self.read_once())
        return rows

    def _read_input(self):
        region = self.region("input")
        with tempfile.TemporaryDirectory(prefix="wechat-ocr-input-") as folder:
            path = Path(folder)/"input.png"
            image = ImageGrab.grab(bbox=region, all_screens=True)
            image.save(path)
            result = recognize_file(path)
            text,ignored = input_draft_text(image,result["lines"])
            if ignored:
                print("[输入区] 已忽略微信灰色提示“长按鼠标语音输入”。",flush=True)
            if text:
                diagnostics = ROOT/"diagnostics"
                diagnostics.mkdir(exist_ok=True)
                image.save(diagnostics/"last_input_check.png")
                (diagnostics/"last_input_check.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
            return text

    def _focus_input(self):
        import win32gui, pyautogui
        region = self.region("input")
        try:
            win32gui.SetForegroundWindow(self.hwnd)
        except Exception:
            # Windows may reject a background process's foreground request.
            # A click in the verified target region can obtain focus normally.
            pass
        pyautogui.click((region[0]+region[2])//2,(region[1]+region[3])//2)
        time.sleep(.3)
        self.validate()
        if win32gui.GetForegroundWindow() != self.hwnd:
            raise RuntimeError("未能获得目标会话焦点")

    def send(self, text):
        import win32gui, pyautogui, pyperclip
        pyautogui.FAILSAFE = True
        # Focus first: placeholder text often disappears only after focus.
        # Never discard arbitrary OCR strings as if all were placeholders.
        self._focus_input()
        detected = self._read_input()
        if detected:
            print("[输入区识别内容] " + detected[:100].replace("\n"," / "),flush=True)
            raise RuntimeError("输入框获得焦点后仍检测到文字，请检查草稿或框选范围；不会覆盖")
        self.region("input")
        if win32gui.GetForegroundWindow() != self.hwnd:
            raise RuntimeError("检查输入框后焦点变化，未粘贴回复")
        pyperclip.copy(text)
        pyautogui.hotkey("ctrl","v")
        time.sleep(.3)
        self.validate()
        if win32gui.GetForegroundWindow() != self.hwnd:
            raise RuntimeError("输入后焦点变化，请检查草稿；不会自动重试")
        pyautogui.hotkey("alt","s")
        # A keyboard shortcut is not a delivery receipt. If text remains, stop
        # without sending again or clearing a draft the user may want to keep.
        for _ in range(3):
            time.sleep(.4)
            self.validate()
            detected = self._read_input()
            if not detected:
                return
        print("[发送后输入区内容] " + detected[:100].replace("\n"," / "),flush=True)
        raise RuntimeError("发送后输入框仍有文字，消息发送未确认；已保留草稿，不会重发，请检查微信发送快捷键")
