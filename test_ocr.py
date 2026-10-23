import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
from contextlib import redirect_stdout
import io
from PIL import Image, ImageDraw
from core import DEFAULTS, Conversation, SnapshotTracker
from ocr_messages import bubble_boxes, messages_from_lines, StableOCRRows, VisualBubbleTracker, visual_observations
from ocr_probe import map_ocr_box
from runtime_health import budget_path, wait_for_snapshot, ReadUnavailable, listening_status
from core import Budget
from runner import try_send_once
from ocr_adapter import OCRWeChatAdapter


class OCRTests(unittest.TestCase):
    def observation(self, text, y, signature=0, side="other", width=80):
        return {"side":side,"box":(0,y,width,y+40),"signature":signature,"text":text}

    def seed_visual(self, observations):
        tracker = VisualBubbleTracker()
        tracker.update(observations)
        return tracker,tracker.update(observations)

    def test_visual_ids_ignore_ocr_spelling_changes(self):
        before = [self.observation("hello",10),self.observation("在吗",80)]
        tracker,baseline = self.seed_visual(before)
        outer = SnapshotTracker()
        outer.update(baseline)
        for spelling in ("仕吗","在 吗","在码","在吗"):
            rows = tracker.update([self.observation("he11o",10),self.observation(spelling,80)])
            added,reset = outer.update(rows)
            self.assertEqual(added,[])
            self.assertFalse(reset)
            self.assertEqual(rows,baseline)

    def test_new_tail_survives_old_ocr_line_disappearing(self):
        old = [self.observation("第一条",10),self.observation("第二条",80)]
        tracker,baseline = self.seed_visual(old)
        outer = SnapshotTracker()
        outer.update(baseline)
        new = [self.observation("",10),self.observation("第错条",80),self.observation("新问题",150)]
        self.assertEqual(tracker.update(new),baseline)
        added,reset = outer.update(tracker.update(new))
        self.assertFalse(reset)
        self.assertEqual(len(added),1)
        self.assertTrue(added[0][1].endswith("新问题"))

    def test_duplicate_phrase_appended_adds_one_event_without_reset(self):
        old = [self.observation("在吗",10),self.observation("在吗",80),self.observation("在吗",150)]
        tracker,baseline = self.seed_visual(old)
        outer = SnapshotTracker()
        outer.update(baseline)
        new = [self.observation("在吗",10),self.observation("在吗",80),
               self.observation("在吗",150),self.observation("在吗",220)]
        tracker.update(new)
        added,reset = outer.update(tracker.update(new))
        self.assertFalse(reset)
        self.assertEqual(len(added),1)
        self.assertNotIn(added[0][0],[row[0] for row in baseline])

    def test_actual_top_scroll_retains_history_and_detects_tail(self):
        a,b,c = 0,(1<<100)-1,(1<<220)-1
        old = [self.observation("A",10,a),self.observation("B",80,b),self.observation("C",150,c)]
        tracker,baseline = self.seed_visual(old)
        outer = SnapshotTracker()
        outer.update(baseline)
        new = [self.observation("B",10,b),self.observation("C",80,c),self.observation("D",150,a)]
        tracker.update(new)
        added,reset = outer.update(tracker.update(new))
        self.assertFalse(reset)
        self.assertEqual(len(added),1)
        self.assertTrue(added[0][1].endswith("D"))
        self.assertEqual(tracker.update(new)[:3],baseline)

    def test_unrelated_scene_and_recovered_history_do_not_trigger(self):
        old = [self.observation("old",10),self.observation("old2",80)]
        tracker,baseline = self.seed_visual(old)
        other = [self.observation("history",10,(1<<400)-1,width=140),
                 self.observation("history2",80,(1<<400)-1,width=140)]
        tracker.update(other)
        self.assertEqual(tracker.update(other),baseline)

    def test_visual_observations_retain_bubbles_with_missing_ocr_text(self):
        image = Image.new("RGB",(300,150),(30,30,30))
        draw = ImageDraw.Draw(image)
        draw.rectangle((5,10,85,50),fill=(47,47,47))
        observed = visual_observations(image,[{"side":"other","box":(5,10,85,50)}],[])
        self.assertEqual(len(observed),1)
        self.assertEqual(observed[0]["text"],"")

    def test_late_ocr_of_older_bubble_is_not_a_new_tail_event(self):
        tracker,baseline = self.seed_visual([self.observation("A",10),self.observation("B",80)])
        new = [self.observation("A",10),self.observation("B",80),
               self.observation("",150),self.observation("D",220)]
        tracker.update(new)
        rows = tracker.update(new)
        self.assertEqual(len(rows),len(baseline)+1)
        new[2] = self.observation("C recovered late",150)
        self.assertEqual(tracker.update(new),rows)

    def test_pending_reply_survives_old_ocr_noise_until_ready(self):
        config = DEFAULTS | {"chat_kind":"private","group":"test"}
        initial = [self.observation("old1",10),self.observation("old2",80)]
        tracker,baseline = self.seed_visual(initial)
        outer = SnapshotTracker()
        outer.update(baseline)
        conversation = Conversation(config)
        conversation.add(baseline,now=0,baseline=True)
        new = initial+[self.observation("new question",150)]
        tracker.update(new)
        added,reset = outer.update(tracker.update(new))
        self.assertFalse(reset)
        conversation.add(added,now=10)
        noisy = [self.observation("different old OCR",10),self.observation("",80),new[-1]]
        added,reset = outer.update(tracker.update(noisy))
        self.assertEqual(added,[])
        self.assertFalse(reset)
        with tempfile.TemporaryDirectory() as folder:
            budget = Budget(config,Path(folder)/"rates.json")
            self.assertTrue(conversation.ready(18,budget))

    def test_tracker_processes_more_than_three_incoming_messages(self):
        active = [self.observation("baseA",10,0,width=50),
                  self.observation("baseB",80,(1<<70)-1,width=70)]
        tracker,baseline = self.seed_visual(active)
        outer = SnapshotTracker()
        outer.update(baseline)
        total = 0
        for i in range(2,10):
            if len(active) >= 3:
                active = [dict(item,box=(item["box"][0],item["box"][1]-70,
                    item["box"][2],item["box"][3]-70)) for item in active[1:]]
            active.append(self.observation("new "+str(i),150,(1<<(i*70))-1,width=50+i*20))
            tracker.update(active)
            added,reset = outer.update(tracker.update(active))
            self.assertFalse(reset)
            self.assertEqual(len(added),1)
            total += len(added)
        self.assertEqual(total,8)

    def test_tall_scroll_with_one_unique_old_tail_keeps_receiving(self):
        a,b,c = 0,(1<<90)-1,(1<<200)-1
        tracker,baseline = self.seed_visual([self.observation("A",10,a),
            self.observation("B",80,b),self.observation("C",150,c)])
        outer = SnapshotTracker()
        outer.update(baseline)
        for i in range(6):
            last = tracker.visible[-1]
            frame = [dict(last,box=(0,10,last["box"][2],50)),
                     self.observation("after tall scroll "+str(i),150,(1<<(260+i*35))-1,width=140+i*20)]
            tracker.update(frame)
            added,reset = outer.update(tracker.update(frame))
            self.assertFalse(reset)
            self.assertEqual(len(added),1)
            self.assertTrue(added[0][1].endswith("after tall scroll "+str(i)))

    def test_single_repeated_or_downward_anchor_cannot_replay_history(self):
        for repeated,downward in ((True,False),(False,True)):
            old = [self.observation("A",10,0),self.observation("B",80,0 if repeated else (1<<100)-1)]
            tracker,baseline = self.seed_visual(old)
            frame = [self.observation("B",150 if downward else 10,old[-1]["signature"]),
                     self.observation("history",220,(1<<300)-1,width=150)]
            tracker.update(frame)
            self.assertEqual(tracker.update(frame),baseline)

    def test_missing_last_text_retries_bubble_once_then_uses_cache(self):
        adapter = OCRWeChatAdapter.__new__(OCRWeChatAdapter)
        adapter.region = Mock(return_value=(0,0,300,180))
        adapter.tail_cache = {}
        image = Image.new("RGB",(300,180),(30,30,30))
        ImageDraw.Draw(image).rounded_rectangle((5,50,95,100),radius=8,fill=(47,47,47))
        full = {"lines":[]}
        retry = {"lines":[{"text":"new text","box":[5,5,70,25]}]}
        with patch("ocr_adapter.ImageGrab.grab",return_value=image),patch("ocr_adapter.recognize_file",side_effect=[full,retry,full]) as recognize:
            first = adapter.read_once()
            second = adapter.read_once()
        self.assertEqual(first[0]["text"],"new text")
        self.assertEqual(second[0]["text"],"new text")
        self.assertEqual(recognize.call_count,3)

    def test_idle_status_does_not_look_like_a_rate_limit(self):
        adapter = Mock(health={"bubbles":5,"texts":4,"tail_unread":True})
        conversation = Mock(first_pending=None)
        budget = Mock()
        status = listening_status(adapter,conversation,budget,None,100,90)
        self.assertIn("等待新消息",status)
        self.assertIn("最新气泡暂未读出",status)
        budget.allowed.assert_not_called()

    def test_model_wait_and_limit_have_distinct_status(self):
        adapter = Mock(health={"bubbles":3,"texts":3})
        conversation = Mock(first_pending=1)
        budget = Mock()
        budget.allowed.return_value = False
        budget.wait_seconds.return_value = 30
        self.assertIn("模型处理中",listening_status(adapter,conversation,budget,object(),100,90))
        self.assertIn("限流等待约 30 秒",listening_status(adapter,conversation,budget,None,100,90))

    def test_dark_and_light_bubble_directions(self):
        for background, incoming in ((30,47),(240,255)):
            image = Image.new("RGB",(500,300),(background,)*3)
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((5,10,130,60),radius=8,fill=(incoming,)*3)
            draw.rounded_rectangle((330,100,490,150),radius=8,fill=(90,190,80))
            boxes = bubble_boxes(image)
            self.assertEqual([box["side"] for box in boxes],["other","self"])

    def test_multiline_text_is_one_bubble_and_timestamp_excluded(self):
        boxes = [{"side":"other","box":(0,10,180,70)},
                 {"side":"self","box":(200,100,300,150)}]
        lines = [{"text":"第一行","box":(10,15,150,30)},
                 {"text":"第二行","box":(10,40,150,55)},
                 {"text":"20:00","box":(150,75,190,90)},
                 {"text":"好的","box":(220,110,280,130)}]
        self.assertEqual(messages_from_lines(boxes,lines),[("other","第一行\n第二行"),("self","好的")])

    def test_repeated_same_text_is_two_messages_and_not_replayed(self):
        tracker = StableOCRRows()
        initial = [("other","hello"),("other","1")]
        self.assertEqual(tracker.update(initial),[])
        rows = tracker.update(initial)
        self.assertEqual(len(rows),2)
        new = initial+[("other","1")]
        self.assertEqual(tracker.update(new),rows)
        updated = tracker.update(new)
        self.assertEqual(len(updated),3)
        self.assertNotEqual(updated[-1][0],updated[-2][0])
        self.assertEqual(tracker.update(new),updated)

    def test_noise_does_not_produce_a_new_message(self):
        tracker = StableOCRRows()
        snapshot = [("other","hello"),("other","你好")]
        tracker.update(snapshot)
        original = tracker.update(snapshot)
        self.assertEqual(tracker.update([("other","he11o"),("other","你好")]),original)
        self.assertEqual(tracker.update(snapshot),original)

    def test_missing_middle_or_scroll_rebaselines_without_trigger(self):
        ocr, outer = StableOCRRows(), SnapshotTracker()
        first = [("other","甲"),("other","乙"),("other","丙")]
        ocr.update(first)
        outer.update(ocr.update(first))
        second = [("other","甲"),("other","丙")]
        ocr.update(second)
        added, reset = outer.update(ocr.update(second))
        self.assertEqual(added,[])
        self.assertTrue(reset)

    def test_ocr_direction_overrides_unreliable_sender_text(self):
        c = Conversation(DEFAULTS | {"chat_kind":"private","group":"TestContact"})
        self.assertTrue(c.is_self("<<SELF>> 我发来的消息"))
        self.assertFalse(c.is_self("<<OTHER>> OCR不需要识别联系人名字"))
        c.add([(1,"<<SELF>> 自己发言")],now=100)
        self.assertIsNone(c.first_pending)
        c.add([(2,"<<OTHER>> 对方发言")],now=101)
        self.assertEqual(c.first_pending,101)

    def test_real_failure_geometry_restores_deskewed_text_to_bubble(self):
        raw = [6,464,177,538]
        mapped = map_ocr_box(raw,6.9,2477,2225,3.5)
        boxes = [{"side":"other","box":(0,71,86,125)}]
        old_box = [(value-24)/3.5 for value in raw]
        self.assertEqual(messages_from_lines(boxes,[{"text":"sample","box":old_box}]),[])
        self.assertEqual(messages_from_lines(boxes,[{"text":"sample","box":mapped}]),[("other","sample")])

    def test_zero_rotation_respects_scale_and_padding(self):
        self.assertEqual(map_ocr_box([44,64,84,104],None,200,200,2),[10,20,30,40])

    def test_preview_quota_cannot_exhaust_live_quota(self):
        with tempfile.TemporaryDirectory() as folder:
            config = DEFAULTS | {"group":"test","hourly_limit":1}
            preview = Budget(config,budget_path(folder,"preview"))
            live = Budget(config,budget_path(folder,"live"))
            preview.reserve(1000)
            self.assertFalse(preview.allowed(1001))
            self.assertTrue(live.allowed(1001))

    def test_temporary_read_failure_waits_instead_of_exiting(self):
        adapter = Mock()
        adapter.snapshot.side_effect = [ReadUnavailable("temporary"),[],[((1,),"text")]]
        sleep = Mock()
        with patch("runtime_health.status_log"),redirect_stdout(io.StringIO()):
            rows,recovered = wait_for_snapshot(adapter,sleep=sleep)
        self.assertTrue(recovered)
        self.assertEqual(rows,[((1,),"text")])
        self.assertEqual(sleep.call_count,2)

    def test_send_exception_does_not_exit_or_retry(self):
        adapter = Mock()
        adapter.send.side_effect = RuntimeError("input unavailable")
        config = {"mode":"live"}
        with patch("runner.status_log"),redirect_stdout(io.StringIO()):
            result = try_send_once(adapter,config,"test")
        self.assertFalse(result)
        adapter.send.assert_called_once_with("test")
        self.assertEqual(config["mode"],"preview")


if __name__ == "__main__":
    unittest.main()
