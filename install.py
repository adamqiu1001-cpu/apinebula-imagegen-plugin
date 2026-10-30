#!/usr/bin/env python3
"""Install the skill as a plain (non-plugin) skill for Codex and/or Claude Code.

Prefer the plugin install described in README.md. This copies
skills/apinebula-imagegen/ without any personal credentials and prepares the
shared Python environment through scripts/run.py.
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys

SOURCE = Path(__file__).resolve().parent / "skills" / "apinebula-imagegen"
TARGETS = {
    # Codex (CLI and ChatGPT desktop) personal skills location.
    "codex": Path.home() / ".agents" / "skills" / SOURCE.name,
    # Claude Code personal skills location (terminal and desktop Code tab).
    "claude": Path.home() / ".claude" / "skills" / SOURCE.name,
}


def copy_skill(target):
    if target.exists():
        raise SystemExit(f"{target} already exists. Preserve it and review an update manually, or pass --destination.")
    shutil.copytree(SOURCE, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".venv"))
    print("Skill copied to:", target, flush=True)


def install(hosts, destination=None, dependencies=True):
    # run.py locates a Python 3.10+ for the environment even if this one is older.
    targets = [destination] if destination else [TARGETS[h] for h in hosts]
    for target in targets:
        copy_skill(target)
    run = targets[0] / "scripts" / "run.py"
    if dependencies:
        subprocess.run([sys.executable, str(run), "--setup"], check=True)
    print("Configure the key for each APINebula group you use, in your own terminal:")
    for group in ("nanobanana", "gpt-image"):
        print(f'  "{sys.executable}" "{run}" configure-key --group {group}')
    print("PowerShell: prefix the quoted command with &")
    print("Start a new Codex / Claude Code conversation and ask for an APINebula image.")
    return targets


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=["codex", "claude", "both"], default="codex",
                        help="codex: ~/.agents/skills; claude: ~/.claude/skills; both")
    parser.add_argument("--destination", type=Path, help="Override the full skill destination folder")
    parser.add_argument("--no-deps", action="store_true", help="Copy only; the environment is created on first use")
    args = parser.parse_args()
    hosts = ["codex", "claude"] if args.host == "both" else [args.host]
    try:
        install(hosts, args.destination, not args.no_deps)
    except subprocess.CalledProcessError:
        raise SystemExit("Environment setup failed; skill files remain. Run scripts/run.py --setup again after fixing network/Python.")
