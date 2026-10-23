"""Distinguish a known faint WeChat voice hint from an actual input draft."""
import math
import re
import numpy as np


def input_draft_text(image, lines):
    gray = np.asarray(image.convert("L"),dtype=float)
    background = float(np.median(gray))
    retained,ignored = [],0
    for line in lines:
        text = line.get("text","").strip()
        if not text:
            continue
        normalized = re.sub(r"\s+","",text)
        box = line.get("box")
        is_hint = False
        if normalized == "长按鼠标语音输入" and box and len(box) == 4:
            left,top,right,bottom = box
            x0,y0 = max(0,math.floor(left)),max(0,math.floor(top))
            x1,y1 = min(image.width,math.ceil(right)),min(image.height,math.ceil(bottom))
            patch = gray[y0:y1,x0:x1]
            if patch.size and top <= max(30,image.height*.3):
                contrast = float(np.percentile(np.abs(patch-background),99))
                is_hint = contrast < 115
        if is_hint:
            ignored += 1
        else:
            retained.append(text)
    return "\n".join(retained),ignored
