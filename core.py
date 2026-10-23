"""Conversation policy, independent of Windows and the network."""
import json
import time
from collections import deque
from pathlib import Path

DEFAULTS = {
    "group": "", "backend": "ocr", "chat_kind": "group", "self_names": "", "api_key": "", "model": "deepseek-flash",
    "persona": "你是群里友善、自然、有分寸的 AI 群友。使用简体中文，通常回复一到三句。",
    "mode": "preview", "context_limit": 60, "poll_seconds": 1,
    "quiet_seconds": 2, "max_wait_seconds": 45, "cooldown_seconds": 180,
    "hourly_limit": 6, "decision_interval": 5, "max_reply_chars": 240,
}


class SnapshotTracker:
    """Only appended UI rows count as new; reloaded history is re-baselined."""
    def __init__(self):
        self.previous = None

    def update(self, rows):
        if not rows:
            return [], False
        if self.previous is None:
            self.previous = rows
            return [], False
        old = self.previous
        self.previous = rows
        # Runtime IDs distinguish consecutive identical messages.
        old_keys = [r[0] for r in old]
        keys = [r[0] for r in rows]
        for n in range(min(len(old), len(rows)), 0, -1):
            if old_keys[-n:] == keys[:n]:
                return rows[n:], False
        # UI virtualization can recreate every runtime ID. Do not replay history.
        return [], True


class Conversation:
    def __init__(self, config):
        self.config = config
        self.history = deque(maxlen=config["context_limit"])
        self.names = [n.strip() for n in config["self_names"].split("|") if n.strip()]
        self.echoes = deque(maxlen=20)
        self.first_pending = None
        self.latest_pending = None
        self.generation = 0
        self.last_decision = -float("inf")

    def is_self(self, raw):
        if raw.startswith("<<SELF>> "):
            return True
        if raw.startswith("<<OTHER>> "):
            return False
        if self.config.get("chat_kind") == "private":
            # Upstream private-chat format: the other person's name prefixes incoming rows.
            target = self.config["group"]
            return not (raw.startswith(target) and raw[len(target):len(target)+1].isspace())
        # Preserve the upstream accessible row text, including the sender.
        for name in self.names:
            if raw == name or (raw.startswith(name) and raw[len(name):len(name)+1].isspace()):
                return True
        return any(raw == e or raw.endswith("\n" + e) or raw.endswith(" " + e) for e in self.echoes)

    def add(self, rows, now=None, baseline=False):
        now = time.time() if now is None else now
        new = 0
        for _, raw in rows:
            raw = raw.strip()
            if not raw:
                continue
            own = self.is_self(raw)
            self.history.append({"time": time.strftime("%H:%M:%S", time.localtime(now)),
                                 "self": own, "new": not baseline and not own, "text": raw[:2000]})
            if not baseline and not own:
                new += 1
        if new:
            self.generation += 1
            if self.first_pending is None:
                self.first_pending = now
            self.latest_pending = now
        return new

    def ready(self, now, budget):
        if self.first_pending is None or not budget.allowed(now):
            return False
        if now - self.last_decision < self.config["decision_interval"]:
            return False
        return (now - self.latest_pending >= self.config["quiet_seconds"] or
                now - self.first_pending >= self.config["max_wait_seconds"])

    def take(self, now):
        self.first_pending = self.latest_pending = None
        self.last_decision = now
        batch = [dict(item) for item in self.history]
        for item in self.history:
            item["new"] = False
        return self.generation, batch


class Budget:
    def __init__(self, config, path):
        self.config, self.path = config, Path(path)
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.attempts = data.get(config["group"], [])
            self.all_groups = data
        else:
            self.attempts, self.all_groups = [], {}

    def allowed(self, now):
        # Future timestamps after a clock rollback also block further sends.
        recent = [t for t in self.attempts if now - t < 3600]
        return (len(recent) < self.config["hourly_limit"] and
                (not self.attempts or now - max(self.attempts) >= self.config["cooldown_seconds"]))

    def reserve(self, now):
        if not self.allowed(now):
            raise RuntimeError("回复频率达到限制")
        self.attempts = [t for t in self.attempts if now - t < 3600] + [now]
        self.all_groups[self.config["group"]] = self.attempts
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.all_groups), encoding="utf-8")
        temp.replace(self.path)

    def wait_seconds(self, now):
        until = now
        if self.attempts:
            until = max(until,max(self.attempts)+self.config["cooldown_seconds"])
        recent = sorted(t for t in self.attempts if now-t < 3600)
        if len(recent) >= self.config["hourly_limit"]:
            until = max(until,recent[-self.config["hourly_limit"]]+3600)
        return max(0,int(until-now+0.999))


def parse_model_decision(content, limit):
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1].rsplit("```", 1)[0]
    data = json.loads(content)
    if not isinstance(data,dict) or not isinstance(data.get("speak"),bool):
        raise ValueError("模型返回必须包含布尔字段 speak，不能当作沉默处理")
    reason = data.get("reason","")
    reason = reason.strip()[:100] if isinstance(reason,str) else ""
    if data["speak"] is False:
        return {"speak":False,"reply":"","reason":reason}
    reply = data.get("reply", "")
    if not isinstance(reply, str):
        raise ValueError("reply 必须是文本")
    if not reply.strip():
        raise ValueError("模型选择发言但 reply 为空，不能当作沉默处理")
    return {"speak":True,"reply":reply.strip()[:limit],"reason":reason}


def parse_decision(content, limit):
    return parse_model_decision(content,limit)["reply"]
