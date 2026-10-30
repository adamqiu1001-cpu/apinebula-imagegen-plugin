#!/usr/bin/env python3
"""Run apinebula_image.py inside a private virtual environment, creating it on first use.

Plugin hosts (Codex / ChatGPT desktop, Claude Code) replace the plugin folder on
every update, so the environment lives in a per-user data folder instead:
  macOS/Linux: ~/.local/share/apinebula-imagegen/   Windows: %LOCALAPPDATA%\\apinebula-imagegen\\
Override with APINEBULA_IMAGEGEN_HOME. Progress goes to stderr; stdout stays JSON.

  python3 run.py <apinebula_image.py arguments>
  python3 run.py --setup       create/verify the environment only
  python3 run.py --self-test   run the offline tests (no key, no paid requests)
"""
import glob
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPT = HERE / "apinebula_image.py"
LOCK = SKILL / "requirements.lock"
MIN = (3, 10)


def log(message):
    sys.stderr.write("[apinebula] " + message + "\n")
    sys.stderr.flush()


def data_root():
    override = os.environ.get("APINEBULA_IMAGEGEN_HOME")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "apinebula-imagegen"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "apinebula-imagegen"


def version_of(command):
    try:
        out = subprocess.run(command + ["-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
                             capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    try:
        major, minor = out.stdout.strip().split(".")
        return int(major), int(minor)
    except ValueError:
        return None


def find_python():
    """Prefer the running interpreter; otherwise look for an installed Python 3.10+."""
    if sys.version_info[:2] >= MIN:
        return [sys.executable]
    candidates = [["python3.14"], ["python3.13"], ["python3.12"], ["python3.11"], ["python3.10"]]
    if os.name == "nt":
        candidates = [["py", "-3"]] + candidates
    else:
        frameworks = sorted(glob.glob("/Library/Frameworks/Python.framework/Versions/3.*/bin/python3"), reverse=True)
        candidates += [[p] for p in frameworks]
    for command in candidates:
        if shutil.which(command[0]) or Path(command[0]).is_file():
            found = version_of(command)
            if found and found >= MIN:
                return command
    sys.stdout.write('{"error": "Python 3.10 or newer is required. Install it from https://www.python.org/downloads/ and retry."}\n')
    sys.exit(1)


def venv_python(venv):
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ensure_env():
    base = find_python()
    tag = "%d.%d" % (tuple(sys.version_info[:2]) if base == [sys.executable] else version_of(base))
    digest = hashlib.sha256(LOCK.read_bytes() + tag.encode()).hexdigest()[:12]
    root = data_root()
    venv = root / ("venv-" + tag + "-" + digest)
    ready = venv / ".apinebula-ready"
    python = venv_python(venv)
    if ready.exists() and python.exists():
        return python
    log("First run: creating a Python environment in " + str(venv) + " (one-time, about a minute)")
    if venv.exists():
        shutil.rmtree(venv)
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(base + ["-m", "venv", str(venv)], check=True)
    subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-q",
                    "-r", str(LOCK)], check=True)
    ready.write_text(digest + "\n", encoding="utf-8")
    # Remove environments built for older requirement sets.
    for old in root.glob("venv-*"):
        if old != venv and old.is_dir():
            shutil.rmtree(old, ignore_errors=True)
    log("Environment ready")
    return python


def main(argv):
    try:
        python = ensure_env()
    except subprocess.CalledProcessError:
        sys.stdout.write('{"error": "Creating the Python environment or installing requirements failed; check network/proxy and retry. No image request was sent."}\n')
        return 1
    if argv[:1] == ["--setup"]:
        sys.stdout.write('{"ready": true, "python": %s}\n' % __import__("json").dumps(str(python)))
        return 0
    if argv[:1] == ["--self-test"]:
        return subprocess.call([str(python), "-m", "unittest", "discover", "-s", str(SKILL / "tests")],
                               cwd=str(data_root()))
    return subprocess.call([str(python), str(SCRIPT)] + argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
