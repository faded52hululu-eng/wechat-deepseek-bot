"""Pixel bubble detection and conservative sequence tracking, with no desktop access."""
from collections import deque
import re
import numpy as np
from PIL import Image


def bubble_boxes(image):
    data = np.asarray(image.convert("RGB"), dtype=np.int16)
    height, width = data.shape[:2]
    edges = np.concatenate((data[0],data[-1],data[:,0],data[:,-1]), axis=0)
    background = float(np.median(edges.mean(axis=1)))
    spread = data.max(axis=2) - data.min(axis=2)
    brightness = data.mean(axis=2)
    if background < 128:
        neutral = (spread < 10) & (brightness >= background + 9) & (brightness <= background + 65)
    else:
        neutral = (spread < 10) & (brightness >= background + 5)
    green = (data[:, :, 1] > data[:, :, 0] + 14) & (data[:, :, 1] > data[:, :, 2] + 10) & (data[:, :, 1] > 65)
    found = []
    for side, original_mask in (("other", neutral), ("self", green)):
        mask = original_mask.copy()
        ys, xs = np.nonzero(mask)
        for y, x in zip(ys, xs):
            if not mask[y, x]:
                continue
            pending = [(int(x), int(y))]
            mask[y, x] = False
            left = right = int(x)
            top = bottom = int(y)
            area = 0
            while pending:
                px, py = pending.pop()
                area += 1
                left, right = min(left, px), max(right, px)
                top, bottom = min(top, py), max(bottom, py)
                for nx, ny in ((px-1, py), (px+1, py), (px, py-1), (px, py+1)):
                    if 0 <= nx < width and 0 <= ny < height and mask[ny, nx]:
                        mask[ny, nx] = False
                        pending.append((nx, ny))
            bw, bh = right-left+1, bottom-top+1
            if bw < 14 or bh < 19 or area < 120 or area/(bw*bh) < 0.40:
                continue
            if bw > width*0.98 or bh > height*0.85:
                continue
            if side == "other" and left > width*0.48:
                continue
            if side == "self" and right < width*0.52:
                continue
            found.append({"side": side, "box": (left, top, right+1, bottom+1)})
    return sorted(found, key=lambda item: item["box"][1])


def messages_from_lines(boxes, lines):
    messages = []
    for bubble in boxes:
        left, top, right, bottom = bubble["box"]
        selected = []
        for line in lines:
            a, b, c, d = line["box"]
            cx, cy = (a+c)/2, (b+d)/2
            if left-3 <= cx <= right+3 and top-3 <= cy <= bottom+3:
                selected.append(line)
        selected.sort(key=lambda item: item["box"][1])
        text = "\n".join(line["text"].strip() for line in selected).strip()
        if text:
            messages.append((bubble["side"], text))
    return messages


def fingerprint(message):
    side, text = message
    # Ignore harmless spacing variations, retain words and repeated bubbles.
    return side, re.sub(r"\s+", "", text)


def visual_observations(image, boxes, lines):
    observations = []
    for bubble in boxes:
        left,top,right,bottom = bubble["box"]
        patch = image.crop((left,top,right,bottom)).convert("L").resize((33,16),Image.Resampling.LANCZOS)
        values = np.asarray(patch)
        bits = np.packbits(values[:,1:] > values[:,:-1]).tobytes()
        message = messages_from_lines([bubble],lines)
        observations.append({"side":bubble["side"],"box":bubble["box"],
                             "signature":int.from_bytes(bits,"big"),
                             "text":message[0][1] if message else ""})
    return observations


class VisualBubbleTracker:
    """Track bubble pixels and vertical movement; OCR spelling never defines IDs.

    Return an append-only event history, independent of visible viewport rows.
    Missing OCR lines or removed top bubbles therefore cannot reset the consumer.
    """
    def __init__(self):
        self.visible = []
        self.rows = deque(maxlen=120)
        self.serial = 0
        self.initialized = False
        self.last_event_id = 0

    @staticmethod
    def similar(a,b):
        aa,bb = a["box"],b["box"]
        return (a["side"] == b["side"] and
                abs((aa[2]-aa[0])-(bb[2]-bb[0])) <= 5 and
                abs((aa[3]-aa[1])-(bb[3]-bb[1])) <= 4 and
                (a["signature"] ^ b["signature"]).bit_count() <= 48)

    @staticmethod
    def center_y(item):
        return (item["box"][1]+item["box"][3])/2

    def align(self, observations):
        candidates = {0.0}
        for old in self.visible:
            for new in observations:
                if self.similar(old,new):
                    candidates.add(self.center_y(new)-self.center_y(old))
        best, best_score = {},(-1,-float("inf"))
        for shift in candidates:
            matches = {}
            last_old = -1
            for j,new in enumerate(observations):
                options = [(abs(self.center_y(old)+shift-self.center_y(new)),i)
                    for i,old in enumerate(self.visible) if i > last_old and self.similar(old,new)
                    and abs(self.center_y(old)+shift-self.center_y(new)) <= 4]
                if options:
                    _,i = min(options)
                    matches[j] = i
                    last_old = i
            score = (len(matches),-abs(shift))
            if score > best_score:
                best,best_score = matches,score
        return best

    def update(self, observations):
        if not observations:
            return list(self.rows)
        matches = self.align(observations) if self.visible else {}
        anchored = bool(matches) and (len(matches) >= 2 or len(self.visible) == 1)
        if len(matches) == 1 and not anchored:
            j,i = next(iter(matches.items()))
            old,new = self.visible[i],observations[j]
            # A tall image can scroll every old bubble except the last one out
            # of view. Accept that tail only with a strong, unique pixel match
            # and upward movement; repeated phrases cannot establish this link.
            strong = (old["signature"] ^ new["signature"]).bit_count() <= 12
            unique_old = sum(self.similar(item,new) for item in self.visible) == 1
            unique_new = sum(self.similar(old,item) for item in observations) == 1
            anchored = (i == len(self.visible)-1 and strong and unique_old and unique_new
                        and self.center_y(new) <= self.center_y(old)+4)
        last_match = max(matches) if matches else -1
        updated = []
        for j,new in enumerate(observations):
            if j in matches:
                old = self.visible[matches[j]]
                item = dict(new,id=old["id"],seen=old["seen"]+1,
                            emitted=old["emitted"],baseline=old["baseline"])
                if not item["text"]:
                    item["text"] = old["text"]
            else:
                self.serial += 1
                # Anything appearing before the last known bubble is recovered
                # history, never a new incoming event.
                is_new_tail = anchored and j > last_match
                item = dict(new,id=self.serial,seen=1,emitted=False,baseline=not is_new_tail)
            if item["seen"] >= 2 and item["text"] and not item["emitted"]:
                if (not self.initialized or not item["baseline"]) and item["id"] > self.last_event_id:
                    prefix = "<<SELF>> " if item["side"] == "self" else "<<OTHER>> "
                    self.rows.append(((item["id"],),prefix+item["text"]))
                    self.last_event_id = item["id"]
                item["emitted"] = True
            updated.append(item)
        self.visible = updated
        if self.rows:
            self.initialized = True
        return list(self.rows)


class StableOCRRows:
    def __init__(self):
        self.candidate = None
        self.streak = 0
        self.messages = []
        self.rows = []
        self.serial = 0

    def update(self, messages):
        keys = [fingerprint(message) for message in messages]
        if not keys:
            return self.rows
        if keys != self.candidate:
            self.candidate, self.streak = keys, 1
            return self.rows
        self.streak += 1
        if self.streak < 2:
            return self.rows
        old = [fingerprint(message) for message in self.messages]
        if keys == old:
            return self.rows
        overlap = 0
        for n in range(min(len(old), len(keys)), 0, -1):
            if old[-n:] == keys[:n]:
                # One repeated phrase is too weak an anchor after a screen scroll.
                if n >= 2 or len(old) == 1:
                    overlap = n
                break
        rows = self.rows[-overlap:] if overlap else []
        for side, text in messages[overlap:]:
            self.serial += 1
            prefix = "<<SELF>> " if side == "self" else "<<OTHER>> "
            rows.append(((self.serial,), prefix + text))
        self.messages, self.rows = messages, rows
        return rows
