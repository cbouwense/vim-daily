#!/usr/bin/env python3
"""Replay every task's reference keys in a real vim and write targets.json.

    python3 tools/build.py            rebuild everything
    python3 tools/build.py 7 12       rebuild lessons 7 and 12 only
    python3 tools/build.py --show 7   also print each task's before and after
    python3 tools/build.py --check    verify targets.json is current, write nothing

Needs a pseudo-terminal, so it runs on macOS and Linux only. Each task is
replayed twice: alone, which produces its target, and between two filler
blocks, which proves the reference keys do not reach outside the task's block.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import pty
import re
import select
import shutil
import struct
import subprocess
import sys
import tempfile
import termios
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import vimdaily as vd  # noqa: E402

FILLER = ["const zqFiller = [1, 2, 3];", "zqFiller.push(4);"]
FINISH = [":", "w", "q", "a", "<CR>"]
ANSI_RE = re.compile(rb"\x1b\[[0-9;?>]*[a-zA-Z]|\x1b[()][0-9A-B]|\x1b[=>]")


class ReplayError(Exception):
    pass


def replay(vim: str, files: Dict[str, List[str]], main: str, tokens: List[str], line: int, lesson: vd.Lesson,
           clean: bool = True) -> Tuple[Dict[str, List[str]], List[str]]:
    """Type tokens into vim. clean=False loads the user's own vimrc and plugins, as a real session does."""
    tmp = Path(tempfile.mkdtemp(prefix="vimdaily-build-"))
    master = -1
    try:
        work = tmp / "work"
        work.mkdir()
        for name, content in files.items():
            (work / name).write_text("\n".join(content) + "\n", encoding="utf-8")
        pane, ready, keylog = tmp / "lesson.txt", tmp / "ready", tmp / "keys.log"
        pane.write_text("replay\n", encoding="utf-8")
        config = {"lesson": pane.as_posix(), "line": line, "arrows": int(lesson.arrows), "opts": lesson.opts,
                  "ready": ready.as_posix()}
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 110, 0, 0))
        isolation = ["-u", "NONE", "-i", "NONE"] if clean else []
        process = subprocess.Popen(
            [vim, "-N", *isolation, "-n", "--cmd", "set ttimeoutlen=5",
             "--cmd", "let g:vimdaily = " + json.dumps(config), "-W", str(keylog), "-S", str(vd.SESSION_VIM), main],
            cwd=str(work), stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
            env=dict(os.environ, TERM="xterm-256color"))
        os.close(slave)
        screen = bytearray()

        def drain(seconds: float) -> None:
            deadline = time.time() + seconds
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return
                readable, _, _ = select.select([master], [], [], min(remaining, 0.02))
                if readable:
                    try:
                        screen.extend(os.read(master, 65536))
                    except OSError:
                        return

        deadline = time.time() + 8
        while not ready.exists():
            if time.time() > deadline or process.poll() is not None:
                process.kill()
                raise ReplayError("vim did not start: " + tail(screen))
            drain(0.02)
        drain(0.05)
        def settle() -> None:
            """Wait until vim stops drawing, as a person would before typing on."""
            deadline = time.time() + 1.5
            while time.time() < deadline:
                readable, _, _ = select.select([master], [], [], 0.025)
                if not readable:
                    return
                try:
                    screen.extend(os.read(master, 65536))
                except OSError:
                    return

        for byte in vd.tokens_to_bytes(tokens + FINISH):
            os.write(master, bytes([byte]))
            # Keys typed ahead of a finished ex command can land in a hit-enter
            # prompt, which swallows j, k, d, u, f, b, g and G.
            if byte == 0x0D:
                settle()
            else:
                drain(0.03 if byte == 0x1B else 0.004)
        deadline = time.time() + 6
        while process.poll() is None:
            if time.time() > deadline:
                process.kill()
                raise ReplayError("vim did not exit after the keys; last screen text: " + tail(screen))
            drain(0.02)
        result = {}
        for path in sorted(work.iterdir()):
            if path.is_file() and not path.name.startswith("."):
                result[path.name] = vd.normalize(path.read_text(encoding="utf-8").split("\n"))
        typed = vd.parse_keylog(keylog.read_bytes()) if keylog.exists() else []
        return result, typed
    finally:
        if master != -1:
            try:
                os.close(master)
            except OSError:
                pass
        shutil.rmtree(tmp, ignore_errors=True)


def tail(screen: bytearray) -> str:
    text = ANSI_RE.sub(b" ", bytes(screen[-1500:])).decode("utf-8", errors="replace")
    return " ".join(text.split())[-300:]


def build_task(vim: str, lesson: vd.Lesson, task: vd.Task) -> dict:
    main = vd.main_name(lesson.ext)
    files, body_line = vd.compose([task], lesson.ext)
    tokens = vd.notation_tokens(vd.resolve_lines(task.keys, body_line.get(1, 1)))
    result, typed = replay(vim, files, main, tokens, body_line.get(1, 1), lesson)
    if typed != tokens + FINISH:
        raise ReplayError(f"keystroke log does not match the keys sent:\n    sent  {tokens + FINISH}\n    read  {typed}")
    target = {"hash": task.digest(lesson.opts), "keys": len(tokens), "main": None, "files": {}}
    if task.main is not None:
        blocks = vd.split_blocks(result.get(main, []))
        if 1 not in blocks:
            raise ReplayError("the reference keys removed the marker line")
        target["main"] = blocks[1]
    for name in task.files:
        target["files"][name] = result.get(name, [])
    unchanged = (task.main is None or target["main"] == vd.normalize(task.main)) and all(
        target["files"][name] == vd.normalize(content) for name, content in task.files.items())
    if unchanged and "noedit" not in task.flags:
        raise ReplayError("the reference keys leave the text unchanged")
    extra = set(result) - set(files)
    if extra:
        raise ReplayError(f"the reference keys created unexpected files: {sorted(extra)}")

    filler_before = [] if "first" in task.flags else [vd.Task(lesson="zz", index=1, main=list(FILLER))]
    filler_after = [] if "last" in task.flags else [vd.Task(lesson="zz", index=2, main=list(FILLER))]
    layout = filler_before + [task] + filler_after
    if len(layout) > 1 and "whole" not in task.flags:
        position = len(filler_before) + 1
        files, body_line = vd.compose(layout, lesson.ext)
        start_line = body_line.get(position, min(body_line.values()))
        shifted = vd.notation_tokens(vd.resolve_lines(task.keys, start_line))
        result, _ = replay(vim, files, main, shifted, start_line, lesson)
        fake_targets = {task.uid: target, "zz.1": {"main": FILLER, "files": {}}, "zz.2": {"main": FILLER, "files": {}}}
        expected, _ = vd.compose(layout, lesson.ext, fake_targets)
        for name, content in expected.items():
            if result.get(name) != vd.normalize(content):
                raise ReplayError(f"the keys behave differently next to other blocks (in {name}):\n" + "\n".join(
                    "      " + row for row in result.get(name, [])))
    return target


def show(task: vd.Task, target: dict) -> None:
    print(f"  --- {task.uid}: {task.text[0]}")
    print(f"      keys ({target['keys']}): {task.keys}")
    pairs = [("", task.main, target["main"])] if task.main is not None else []
    pairs += [(name + ": ", content, target["files"][name]) for name, content in task.files.items()]
    for label, before, after in pairs:
        for row in before:
            print(f"      {label}< {row}")
        for row in after:
            print(f"      {label}> {row}")


def check_requires(lesson: vd.Lesson) -> List[str]:
    if not lesson.verify:
        return []
    typed = "".join(token for task in lesson.tasks
                    for token in vd.notation_tokens(vd.resolve_lines(task.keys, 2)) + FINISH)
    problems = []
    for regex, label in lesson.require:
        try:
            if not re.search(regex, typed):
                problems.append(f"lesson {lesson.id}: no reference solution uses the required key {label!r} ({regex})")
        except re.error as error:
            problems.append(f"lesson {lesson.id}: bad require regex {regex!r}: {error}")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("lessons", nargs="*", type=int, help="lesson numbers (default: all)")
    parser.add_argument("--show", action="store_true", help="print each task's before and after")
    parser.add_argument("--check", action="store_true", help="fail if targets.json is out of date; write nothing")
    parser.add_argument("--dry-run", action="store_true", help="replay and validate, but do not write targets.json")
    args = parser.parse_args()

    vim = vd.require_vim()
    lessons = vd.load_lessons()
    targets = vd.load_targets()
    problems: List[str] = []

    seen_files: Dict[str, str] = {}
    for lesson in lessons:
        for task in lesson.tasks:
            if "first" in task.flags and task.index != 1:
                problems.append(f"{task.uid}: only a lesson's first task can be flagged 'first'")
            for name in task.files:
                if name in seen_files or name == vd.main_name(lesson.ext):
                    problems.append(f"{task.uid}: file name {name} is already used by {seen_files.get(name, 'the practice file')}")
                seen_files[name] = task.uid

    if args.check:
        for lesson in lessons:
            for task in lesson.tasks:
                if task.keys and targets.get(task.uid, {}).get("hash") != task.digest(lesson.opts):
                    problems.append(f"{task.uid}: targets.json is out of date")
        report(problems)
        return

    selected = [l for l in lessons if l.kind == "edit" and (not args.lessons or l.num in args.lessons)]
    jobs = [(lesson, task) for lesson in selected for task in lesson.tasks]

    def run(job: Tuple[vd.Lesson, vd.Task]) -> Tuple[vd.Task, Optional[dict], Optional[str]]:
        lesson, task = job
        if not task.keys:
            return task, None, "no reference keys"
        try:
            return task, build_task(vim, lesson, task), None
        except ReplayError as error:
            return task, None, str(error)

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(run, jobs))
    for task, target, error in results:
        if error:
            problems.append(f"{task.uid}: {error}")
        else:
            assert target is not None
            previous = targets.get(task.uid)
            if previous and previous["hash"] == target["hash"] and previous != target:
                problems.append(f"{task.uid}: this vim produces a different result than the one in targets.json")
            targets[task.uid] = target
            if args.show:
                show(task, target)
    for lesson in selected:
        problems += check_requires(lesson)

    live = {task.uid for lesson in lessons for task in lesson.tasks}
    targets = {uid: targets[uid] for uid in sorted(targets) if uid in live}
    if not args.dry_run:
        vd.TARGETS_FILE.write_text(json.dumps(targets, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(jobs) - sum(1 for _, _, e in results if e)} of {len(jobs)} tasks built.")
    report(problems)


def report(problems: List[str]) -> None:
    for problem in problems:
        print("PROBLEM " + problem)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
