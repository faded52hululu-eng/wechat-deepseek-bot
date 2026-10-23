"""Small adapter using pywechat's pyweixin UI definitions, pinned in setup."""
import time
import re
import os


def is_target_window(title, process_name, expected):
    return title == expected and process_name.lower() in {"weixin.exe", "wechat.exe"}


class WeChatAdapter:
    def __init__(self, group):
        from pyweixin.Uielements import Lists, Edits
        from pywinauto import Desktop
        import psutil
        import pyautogui
        # Upstream imports disable this global; retain the emergency mouse stop.
        pyautogui.FAILSAFE = True
        self.group, self.Lists, self.Edits = group, Lists, Edits
        # WeChat 4.1.15 may use a different native window class. Match the exact
        # returned title and owning executable instead of a legacy class name.
        matches = []
        for window in Desktop(backend="uia").windows():
            if window.window_text() != group:
                continue
            try:
                process_name = psutil.Process(window.process_id()).name()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if is_target_window(window.window_text(), process_name, group):
                matches.append(window)
        if len(matches) != 1:
            raise RuntimeError(f"找到 {len(matches)} 个标题完全等于「{group}」的微信窗口。请核对独立会话窗口标题与联系人备注名；重复窗口请关闭多余的。")
        print("已找到目标微信会话窗口，正在检查消息列表……", flush=True)
        self.window = Desktop(backend="uia").window(handle=matches[0].handle)
        self.handle = self.window.handle
        self.assert_target()
        self.chat = self.window.child_window(**Lists.FriendChatList)
        if not self.chat.exists(timeout=3):
            raise RuntimeError("已找到会话窗口，但微信没有暴露可读取的消息列表（UI 无障碍树不可用或布局不兼容）。当前接入方案无法监听；重复点启动或发送消息不会解决。尚未调用 DeepSeek。")
        if self.window.is_minimized():
            raise RuntimeError("首次检测请保持独立群窗口展开，且消息列表滚动到最下方。")

    def assert_target(self):
        if not self.window.exists() or self.window.handle != self.handle or self.window.window_text() != self.group:
            raise RuntimeError("目标群窗口已关闭或标题发生变化，已停止；不会改发其他会话。")

    def snapshot(self):
        self.assert_target()
        rows = []
        # Normal mode only. Selection mode has different controls and is rejected.
        if self.chat.children(control_type="CheckBox"):
            raise RuntimeError("请退出群消息多选状态，然后重新启动。")
        for item in self.chat.children(control_type="ListItem"):
            cls = item.class_name()
            if cls in {"mmui::ChatItemView", "mmui::ChatSystemInfoItemView"}:
                continue
            raw = item.window_text().strip()
            if not raw:
                continue
            if any(tag in raw for tag in ("[图片]", "[视频]", "[语音]", "[文件]")):
                continue
            if re.fullmatch(r"[\d\s:/年月日星期一二三四五六天昨天上午下午晚上]+", raw):
                continue
            rid = item.element_info.runtime_id
            if rid is None:
                raise RuntimeError("微信没有提供稳定消息标识，无法可靠去重，已停止。")
            rows.append((tuple(rid), raw))
        return rows[-120:]

    def send(self, text):
        import win32gui
        self.assert_target()
        if self.window.is_minimized():
            raise RuntimeError("发送前请展开独立群窗口。")
        edit = self.window.child_window(**self.Edits.CurrentChatEdit)
        if not edit.exists(timeout=1):
            raise RuntimeError("没有找到目标群输入框")
        if edit.window_text().strip():
            raise RuntimeError("目标群输入框已有草稿，为避免覆盖已停止。")
        # Scope all input to the previously verified independent window.
        self.window.set_focus()
        self.assert_target()
        if win32gui.GetForegroundWindow() != self.handle:
            raise RuntimeError("无法取得目标群窗口焦点，已停止")
        edit.set_edit_text(text)
        time.sleep(0.2)
        self.assert_target()
        if win32gui.GetForegroundWindow() != self.handle:
            raise RuntimeError("窗口焦点发生变化，回复可能留在草稿中，请手动检查")
        edit.type_keys("%s", set_foreground=False)
        # Never retry automatically: sending is an attempt, not a delivery receipt.
