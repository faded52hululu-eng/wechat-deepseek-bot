import unittest
from unittest.mock import Mock, patch
from contextlib import redirect_stdout
import io
from ocr_adapter import OCRWeChatAdapter
from input_guard import input_draft_text
from PIL import Image,ImageDraw


class SendTests(unittest.TestCase):
    def test_known_gray_voice_hint_is_not_a_draft(self):
        image = Image.new("RGB",(300,100),(30,30,30))
        ImageDraw.Draw(image).rectangle((5,5,180,25),fill=(70,70,70))
        lines = [{"text":"长按鼠标语音输入","box":[5,5,180,25]}]
        text,ignored = input_draft_text(image,lines)
        self.assertEqual(text,"")
        self.assertEqual(ignored,1)

    def test_actual_bright_draft_with_same_words_is_preserved(self):
        image = Image.new("RGB",(300,100),(30,30,30))
        ImageDraw.Draw(image).rectangle((5,5,180,25),fill=(230,230,230))
        text,ignored = input_draft_text(image,[{"text":"长按鼠标语音输入","box":[5,5,180,25]}])
        self.assertEqual(text,"长按鼠标语音输入")
        self.assertEqual(ignored,0)

    def test_gray_hint_does_not_hide_other_draft_text(self):
        image = Image.new("RGB",(300,100),(30,30,30))
        ImageDraw.Draw(image).rectangle((5,5,180,25),fill=(70,70,70))
        text,_ = input_draft_text(image,[{"text":"长按鼠标语音输入","box":[5,5,180,25]},
            {"text":"真实草稿","box":[5,40,180,65]}])
        self.assertEqual(text,"真实草稿")

    def adapter(self):
        adapter = OCRWeChatAdapter.__new__(OCRWeChatAdapter)
        adapter.hwnd = 123
        adapter.validate = Mock()
        adapter.region = Mock(return_value=(10,20,110,80))
        return adapter

    def modules(self):
        return {"win32gui":Mock(GetForegroundWindow=lambda:123),
                "pyautogui":Mock(),"pyperclip":Mock()}

    def test_placeholder_check_occurs_after_focus(self):
        adapter,modules,events = self.adapter(),self.modules(),[]
        adapter._focus_input = Mock(side_effect=lambda:events.append("focus"))
        adapter._read_input = Mock(side_effect=lambda:events.append("read") or "")
        modules["pyperclip"].copy.side_effect = lambda _:events.append("paste")
        with patch.dict("sys.modules",modules),patch("ocr_adapter.time.sleep"):
            adapter.send("reply")
        self.assertEqual(events,["focus","read","paste","read"])

    def test_real_draft_is_not_overwritten(self):
        adapter,modules = self.adapter(),self.modules()
        adapter._focus_input = Mock()
        adapter._read_input = Mock(return_value="unsent draft")
        with patch.dict("sys.modules",modules),redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):
                adapter.send("reply")
        modules["pyperclip"].copy.assert_not_called()
        modules["pyautogui"].hotkey.assert_not_called()

    def test_uncleared_input_is_not_reported_as_success_or_retried(self):
        adapter,modules = self.adapter(),self.modules()
        adapter._focus_input = Mock()
        adapter._read_input = Mock(side_effect=["","reply","reply","reply"])
        with patch.dict("sys.modules",modules),patch("ocr_adapter.time.sleep"),redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):
                adapter.send("reply")
        keys = modules["pyautogui"].hotkey.call_args_list
        self.assertEqual([call.args for call in keys],[("ctrl","v"),("alt","s")])
        modules["pyperclip"].copy.assert_called_once_with("reply")


if __name__ == "__main__":
    unittest.main()
