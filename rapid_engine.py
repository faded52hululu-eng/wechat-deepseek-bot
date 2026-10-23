"""Local Chinese OCR; keep the model loaded across polling iterations."""
import os
import re
import threading
import numpy as np
from PIL import Image, ImageOps

_engine = None
_lock = threading.Lock()


def recognize(path):
    if os.environ.get("WECHAT_OCR_ENGINE", "").lower() == "windows":
        from ocr_probe import recognize_windows_file
        return recognize_windows_file(path)
    global _engine
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise RuntimeError("RapidOCR 尚未安装，请先运行 setup.cmd 更新依赖。") from exc
    with Image.open(path) as original:
        image = original.convert("RGB")
    if float(np.median(np.asarray(image.convert("L")))) < 100:
        image = ImageOps.invert(image)
    scale = min(2.0, 1600 / max(image.size))
    image = image.resize((round(image.width*scale),round(image.height*scale)),Image.Resampling.LANCZOS)
    border = 24
    image = ImageOps.expand(image,border=border,fill=image.getpixel((image.width-1,image.height-1)))
    pixels = np.asarray(image)[:,:,::-1].copy()
    with _lock:
        if _engine is None:
            _engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
        results, _ = _engine(pixels)
    lines = []
    for quad,text,confidence in results or []:
        xs,ys = zip(*quad)
        lines.append({"text":re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "",text),
                      "box":[(min(xs)-border)/scale,(min(ys)-border)/scale,
                             (max(xs)-border)/scale,(max(ys)-border)/scale],
                      "confidence":float(confidence)})
    return {"engine":"RapidOCR","language":"zh-CN","angle":0,"lines":lines}
