import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class ReadUnavailable(RuntimeError):
    """A temporary lack of readable pixels; never send while this is active."""


def status_log(message):
    folder = ROOT/"diagnostics"
    folder.mkdir(exist_ok=True)
    path = folder/"runtime_status.log"
    if path.exists() and path.stat().st_size > 500_000:
        path.replace(folder/"runtime_status.previous.log")
    with path.open("a",encoding="utf-8") as stream:
        stream.write(time.strftime("%Y-%m-%d %H:%M:%S ") + message + "\n")


def budget_path(root, mode):
    return Path(root)/("preview_rate_state.json" if mode == "preview" else "rate_state.json")


def listening_status(adapter, conversation, budget, future, now, last_incoming):
    health = getattr(adapter,"health",{})
    detail = (f"画面 {health.get('bubbles',0)} 个气泡 / {health.get('texts',0)} 条可读文字；"
              if health else "")
    if health.get("tail_unread"):
        detail += "最新气泡暂未读出，正在重试；"
    if future is not None:
        state = "模型处理中"
    elif conversation.first_pending is not None and not budget.allowed(now):
        state = f"限流等待约 {budget.wait_seconds(now)} 秒"
    elif conversation.first_pending is not None:
        state = "等待聊天停顿后接话"
    else:
        state = "等待新消息"
    age = "本次尚无新来信" if last_incoming is None else f"上次新来信 {max(0,int(now-last_incoming))} 秒前"
    return "监听中：" + detail + state + "；" + age


def wait_for_snapshot(adapter, sleep=time.sleep):
    paused = False
    last_notice = -float("inf")
    while True:
        try:
            rows = adapter.snapshot()
            if not rows:
                raise ReadUnavailable("等待连续两次稳定的气泡识别")
            if paused:
                print("读取已恢复，继续监听；暂停期间不会发送消息。",flush=True)
                status_log("读取恢复")
            return rows,paused
        except (ReadUnavailable,TimeoutError) as exc:
            paused = True
            if time.monotonic()-last_notice >= 20:
                message = "读取暂停，自动等待恢复：" + str(exc)
                print(message,flush=True)
                status_log(message)
                last_notice = time.monotonic()
            sleep(2)
