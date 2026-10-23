import json
import tempfile
import unittest
from unittest.mock import Mock, patch
from adapter import WeChatAdapter, is_target_window
from pathlib import Path
from core import DEFAULTS, SnapshotTracker, Conversation, Budget, parse_decision, parse_model_decision


class PolicyTests(unittest.TestCase):
    def config(self):
        return DEFAULTS | {"group": "测试群", "self_names": "我的群昵称|我的昵称"}

    def test_startup_does_not_reply_and_self_is_ignored(self):
        c = Conversation(self.config())
        c.add([(1, "甲 旧消息")], now=100, baseline=True)
        c.add([(2, "我的群昵称 自己发言")], now=102)
        self.assertIsNone(c.first_pending)
        c.add([(3, "乙 新问题")], now=105)
        self.assertEqual(c.first_pending, 105)

    def test_quiet_period_and_continuous_chat(self):
        with tempfile.TemporaryDirectory() as d:
            c = Conversation(self.config() | {"quiet_seconds":8})
            b = Budget(self.config(), Path(d)/"rates.json")
            c.add([(1, "甲 问题")], now=100)
            self.assertFalse(c.ready(107, b))
            self.assertTrue(c.ready(108, b))
            c.add([(2, "乙 继续")], now=144)
            self.assertTrue(c.ready(145, b))

    def test_runtime_ids_preserve_identical_messages_and_reset_is_safe(self):
        t = SnapshotTracker()
        self.assertEqual(t.update([((1,), "甲 哈哈")]), ([], False))
        self.assertEqual(t.update([((1,), "甲 哈哈"), ((2,), "甲 哈哈")])[0], [((2,), "甲 哈哈")])
        self.assertEqual(t.update([((8,), "甲 哈哈")]), ([], True))

    def test_rate_limit_persists_across_restart_and_clock_rollback(self):
        with tempfile.TemporaryDirectory() as d:
            config = self.config() | {"hourly_limit": 2}
            path = Path(d)/"rate.json"
            b = Budget(config, path)
            b.reserve(1000)
            b = Budget(config, path)
            self.assertFalse(b.allowed(999))
            self.assertFalse(b.allowed(1100))
            self.assertTrue(b.allowed(1180))
            b.reserve(1180)
            self.assertFalse(b.allowed(1500))
            self.assertTrue(b.allowed(4781))

    def test_model_cannot_override_policy_by_string_boolean(self):
        with self.assertRaises(ValueError):
            parse_decision('{"speak":"true","reply":"乱说"}', 10)
        self.assertEqual(parse_decision('{"speak":false}', 10), "")
        self.assertEqual(parse_decision('{"speak":true,"reply":"123456789"}', 3), "123")
        with self.assertRaises(json.JSONDecodeError):
            parse_decision("模型返回了非 JSON", 10)

    def test_only_current_incoming_batch_is_marked_new(self):
        c = Conversation(self.config())
        c.add([(1,"<<OTHER>> background")],now=0,baseline=True)
        c.add([(2,"<<SELF>> own")],now=1)
        c.add([(3,"<<OTHER>> current")],now=2)
        _,batch = c.take(4)
        self.assertEqual([row["new"] for row in batch],[False,False,True])
        self.assertTrue(batch[-1]["new"])
        c.add([(4,"<<OTHER>> next")],now=5)
        _,next_batch = c.take(7)
        self.assertEqual([row["new"] for row in next_batch],[False,False,False,True])
        self.assertTrue(batch[-1]["new"])

    def test_bad_schema_or_empty_reply_is_error_not_silence(self):
        for raw in ('{}','[]','{"speak":true,"reply":""}','{"speak":true,"reply":123}'):
            with self.assertRaises(ValueError):
                parse_model_decision(raw,100)
        data = parse_model_decision('{"speak":false,"reason":"无可理解文字"}',100)
        self.assertEqual(data["reason"],"无可理解文字")
        self.assertFalse(data["speak"])

    def test_stale_reply_generation_changes_only_for_incoming(self):
        c = Conversation(self.config())
        c.add([(1, "甲 问题")], now=1)
        generation, _ = c.take(10)
        c.add([(2, "我的群昵称 自己发言")], now=11)
        self.assertEqual(generation, c.generation)
        c.add([(3, "乙 新话题")], now=12)
        self.assertNotEqual(generation, c.generation)

    def test_window_name_change_blocks_reading(self):
        adapter = WeChatAdapter.__new__(WeChatAdapter)
        adapter.group, adapter.handle = "目标群", 123
        adapter.window = Mock(handle=123)
        adapter.window.exists.return_value = True
        adapter.window.window_text.return_value = "另一个群"
        with self.assertRaises(RuntimeError):
            adapter.snapshot()

    def test_private_chat_classifies_target_and_own_messages(self):
        c = Conversation(self.config() | {"chat_kind": "private", "group": "TestContact", "self_names": ""})
        self.assertFalse(c.is_self("TestContact 你好"))
        self.assertTrue(c.is_self("自己的回复"))
        self.assertTrue(c.is_self("机器人昵称 你好"))
        self.assertTrue(c.is_self("TestContact类似名字 不能误判"))

    def test_target_matching_checks_title_and_process_not_old_class(self):
        self.assertTrue(is_target_window("TestContact", "Weixin.exe", "TestContact"))
        self.assertFalse(is_target_window("TestContact", "chrome.exe", "TestContact"))
        self.assertFalse(is_target_window("TestContact2", "Weixin.exe", "TestContact"))

    def test_focus_loss_prevents_writing(self):
        adapter = WeChatAdapter.__new__(WeChatAdapter)
        adapter.group, adapter.handle = "目标群", 123
        adapter.window = Mock(handle=123)
        adapter.window.exists.return_value = True
        adapter.window.window_text.return_value = "目标群"
        adapter.window.is_minimized.return_value = False
        adapter.Edits = Mock(CurrentChatEdit={"auto_id": "input"})
        edit = adapter.window.child_window.return_value
        edit.exists.return_value = True
        edit.window_text.return_value = ""
        with patch.dict("sys.modules", {"win32gui": Mock(GetForegroundWindow=lambda: 999)}):
            with self.assertRaises(RuntimeError):
                adapter.send("这条不能发")
        edit.set_edit_text.assert_not_called()
        edit.type_keys.assert_not_called()


if __name__ == "__main__":
    unittest.main()
