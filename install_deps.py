"""Download a fixed upstream snapshot rather than silently switching versions."""
import io
import os
import shutil
import socket
import subprocess
import sys
import urllib.request
import urllib.error
import urllib.parse
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
COMMIT = "8589baa049bb91d3a500602c167f07b2f8397a13"
URL = f"https://codeload.github.com/Hello-Mr-Crab/pywechat/zip/{COMMIT}"
target = ROOT / "upstream"
target.mkdir(exist_ok=True)
archive = target / "source.zip"
source_root = target / ("pywechat-" + COMMIT)
print("Downloading pinned pywechat source: " + COMMIT, flush=True)
if not (source_root / "src" / "setup.py").exists():
    git = shutil.which("git")
    if git:
        source_root.mkdir(exist_ok=True)
        subprocess.run([git, "init", str(source_root)], check=True)
        subprocess.run([git, "-C", str(source_root), "-c", "http.proxy=", "-c", "https.proxy=",
            "fetch", "--depth", "1", "https://github.com/Hello-Mr-Crab/pywechat.git", COMMIT], check=True)
        subprocess.run([git, "-C", str(source_root), "checkout", "--detach", "FETCH_HEAD"], check=True)
    else:
        if not archive.exists():
            try:
                data = urllib.request.urlopen(URL, timeout=15).read()
            except (urllib.error.URLError, TimeoutError):
                print("Configured network path failed; trying a direct connection...", flush=True)
                data = urllib.request.build_opener(urllib.request.ProxyHandler({})).open(URL, timeout=45).read()
            archive.write_bytes(data)
        with zipfile.ZipFile(archive) as z:
            for item in z.infolist():
                resolved = (target / item.filename).resolve()
                if not resolved.is_relative_to(target.resolve()):
                    raise RuntimeError("Invalid archive path")
            z.extractall(target)
source = source_root / "src"
install_env = os.environ.copy()
# The local machine may retain a stopped loopback proxy. Do not alter system settings.
for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
    value = install_env.get(key)
    if not value:
        continue
    parsed = urllib.parse.urlparse(value)
    if parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        try:
            with socket.create_connection((parsed.hostname, parsed.port or 80), timeout=1):
                pass
        except OSError:
            print("Ignoring an unavailable local proxy for this installation only.", flush=True)
            for proxy_key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
                install_env.pop(proxy_key, None)
            break
subprocess.run([sys.executable, "-m", "pip", "install", str(source),
                "beautifulsoup4>=4.12", "markdownify>=1.2.2",
                "rapidocr_onnxruntime==1.4.4"], check=True, env=install_env)
print("Dependencies installed.", flush=True)
