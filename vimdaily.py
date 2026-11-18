#!/usr/bin/env python3
"""vim-daily: one vim concept per weekday, with a checked practice file.

Runs on the Python 3.9+ standard library alone, on macOS, Linux and Windows.
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parent
LESSONS_DIR = ROOT / "lessons"
TARGETS_FILE = ROOT / "targets.json"
PROGRESS_FILE = ROOT / "progress.log"
SESSION_VIM = ROOT / "session.vim"
CONFIG_FILE = ROOT / "config.json"

IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"

COMMENT_PREFIX = {"ts": "//", "java": "//", "js": "//", "py": "#", "sh": "#", "yaml": "#", "vim": '"'}
MARKER_RE = re.compile(r"^\S{1,2} \[(\d+)\]$")
LINE_REF_RE = re.compile(r"\{L(?:\+(\d+))?\}")
EMPTY_MAIN = "(nothing to edit in this file: today's tasks are in other files)"
REVIEW_SIZE = 6
REVIEW_TASK_MAX_LINES = 15
LAUNCHD_LABEL = "com.vimdaily.lesson"
TASK_NAME = "VimDaily"


# ---------------------------------------------------------------- lessons


@dataclass
class Task:
    lesson: str
    index: int
    text: List[str] = field(default_factory=list)
    main: Optional[List[str]] = None
    files: Dict[str, List[str]] = field(default_factory=dict)
    keys: str = ""
    flags: Set[str] = field(default_factory=set)

    @property
    def uid(self) -> str:
        return f"{self.lesson}.{self.index}"

    def digest(self, opts: str) -> str:
        payload = json.dumps([self.main, self.files, self.keys, opts], sort_keys=True)
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


@dataclass
class Lesson:
    id: str
    num: int
    title: str
    keys: str
    ext: str
    opts: str
    arrows: bool
    verify: bool
    kind: str
    require: List[Tuple[str, str]]
    vimrc: List[Tuple[str, str]]
    body: List[str]
    tasks: List[Task]


def strip_blank_edges(lines: List[str]) -> List[str]:
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def parse_tasks(lines: List[str], lesson_id: str, where: str) -> List[Task]:
    tasks: List[Task] = []
    task: Optional[Task] = None
    snippet: Optional[List[str]] = None
    in_text = False
    for line in lines:
        if line.startswith("== "):
            if not in_text:
                task = Task(lesson=lesson_id, index=len(tasks) + 1)
                tasks.append(task)
                snippet = None
                in_text = True
            assert task is not None
            task.text.append(line[3:])
            continue
        in_text = False
        if task is None:
            continue
        if line.startswith("@ "):
            attr = line[2:].strip()
            if attr.startswith("file:"):
                snippet = task.files.setdefault(attr[5:].strip(), [])
            else:
                task.flags.add(attr)
        elif line.startswith("-- "):
            task.keys += line[3:]
        elif task.keys:
            if line.strip():
                raise ValueError(f"{where}: text after the keys of task {task.index}: {line!r}")
        else:
            if snippet is None:
                task.main = []
                snippet = task.main
            snippet.append(line)
    for t in tasks:
        if t.main is not None:
            t.main = strip_blank_edges(t.main)
        t.files = {name: strip_blank_edges(content) for name, content in t.files.items()}
    return tasks


def parse_lesson(path: Path) -> Lesson:
    lines = path.read_text(encoding="utf-8").split("\n")
    lesson_id = path.name.split("-", 1)[0]
    head: Dict[str, str] = {}
    require: List[Tuple[str, str]] = []
    vimrc: List[Tuple[str, str]] = []
    i = 0
    while i < len(lines) and lines[i].strip():
        key, _, value = lines[i].partition(":")
        key, value = key.strip(), value.strip()
        if key in ("require", "vimrc"):
            regex, _, label = value.partition(" :: ")
            (require if key == "require" else vimrc).append((regex.strip(), label.strip() or regex.strip()))
        else:
            head[key] = value
        i += 1
    rest = lines[i:]
    first_task = next((n for n, line in enumerate(rest) if line.startswith("== ")), len(rest))
    tasks = parse_tasks(rest[first_task:], lesson_id, path.name)
    if head.get("opts"):
        for task in tasks:
            task.flags.add("own-options")
    return Lesson(
        id=lesson_id,
        num=int(lesson_id),
        title=head.get("title", path.stem),
        keys=head.get("keys", ""),
        ext=head.get("ext", "ts"),
        opts=head.get("opts", ""),
        arrows=head.get("arrows", "no") == "yes",
        verify=head.get("verify", "yes") != "no",
        kind=head.get("kind", "edit"),
        require=require,
        vimrc=vimrc,
        body=strip_blank_edges(rest[:first_task]),
        tasks=tasks,
    )


def load_lessons() -> List[Lesson]:
    paths = sorted(p for p in LESSONS_DIR.glob("*.txt") if re.match(r"\d+-", p.name))
    return [parse_lesson(p) for p in paths]


def load_targets() -> Dict[str, dict]:
    if not TARGETS_FILE.exists():
        return {}
    return json.loads(TARGETS_FILE.read_text(encoding="utf-8"))


# ------------------------------------------------------------ composition


def main_name(ext: str) -> str:
    return ("Practice" if ext == "java" else "practice") + "." + ext


def marker(ext: str, number: int) -> str:
    return f"{COMMENT_PREFIX.get(ext, '//')} [{number}]"


def compose(tasks: List[Task], ext: str, targets: Optional[Dict[str, dict]] = None) -> Tuple[Dict[str, List[str]], Dict[int, int]]:
    """Lay tasks out as files. Returns ({file name: lines}, {task number: first body line})."""
    files: Dict[str, List[str]] = {}
    main: List[str] = []
    body_line: Dict[int, int] = {}
    for number, task in enumerate(tasks, 1):
        target = targets[task.uid] if targets is not None else None
        if task.main is not None:
            if main:
                main.append("")
            main.append(marker(ext, number))
            body_line[number] = len(main) + 1
            main.extend(target["main"] if target else task.main)
        for name, content in task.files.items():
            files[name] = list(target["files"][name] if target else content)
    if not main:
        main = [f"{COMMENT_PREFIX.get(ext, '//')} {EMPTY_MAIN}"]
    files[main_name(ext)] = main
    return files, body_line


def resolve_lines(text: str, body_line: int) -> str:
    """Replace {L} and {L+n} with file line numbers counted from a block's first body line."""
    return LINE_REF_RE.sub(lambda m: str(body_line + int(m.group(1) or 0)), text)


def normalize(lines: List[str]) -> List[str]:
    out = [line.rstrip() for line in lines]
    while out and not out[-1]:
        out.pop()
    return out


def split_blocks(lines: List[str]) -> Dict[int, List[str]]:
    blocks: Dict[int, List[str]] = {}
    current: Optional[List[str]] = None
    for line in lines:
        m = MARKER_RE.match(line.rstrip())
        if m:
            current = blocks.setdefault(int(m.group(1)), [])
        elif current is not None:
            current.append(line)
    return {number: normalize(body) for number, body in blocks.items()}


def pick(seed: str, count: int) -> int:
    return int(hashlib.sha1(seed.encode("utf-8")).hexdigest(), 16) % count


def reusable(task: Task, allow_files: bool) -> bool:
    if task.flags & {"first", "last", "own-options"} or not task.keys:
        return False
    return allow_files or not task.files


def lesson_tasks(lesson: Lesson, lessons: List[Lesson]) -> Tuple[List[Task], Set[str]]:
    """A lesson's own tasks plus one or two from earlier lessons. Returns (tasks, review uids)."""
    earlier = [l for l in lessons if l.num < lesson.num and l.kind == "edit"]
    # Targets are built under each lesson's own options, so a lesson that changes
    # them neither lends its tasks nor borrows any.
    if lesson.kind != "edit" or lesson.opts or not earlier:
        return list(lesson.tasks), set()
    def brief(task: Task) -> bool:
        return reusable(task, False) and len(task.main or []) <= REVIEW_TASK_MAX_LINES

    recent = [t for l in earlier[-4:] for t in l.tasks if brief(t)]
    older = [t for l in earlier[:-4] for t in l.tasks if brief(t)]
    pulled: List[Task] = []
    if recent:
        pulled.append(recent[pick(f"{lesson.id}:recent", len(recent))])
    if lesson.num > 3:
        pool = [t for t in (older or recent) if t not in pulled]
        if pool:
            pulled.append(pool[pick(f"{lesson.id}:older", len(pool))])
    own = [t for t in lesson.tasks if "last" not in t.flags]
    last = [t for t in lesson.tasks if "last" in t.flags]
    return own + pulled + last, {t.uid for t in pulled}


def review_tasks(pool: List[Lesson], fallback: List[Lesson], seed: str) -> List[Task]:
    """Up to REVIEW_SIZE tasks, taken round-robin from the lessons under review."""
    chosen: List[Task] = []
    for source in (pool, [l for l in fallback if l not in pool]):
        candidates = {l.id: [t for t in l.tasks if reusable(t, True)] for l in source}
        round_number = 0
        while len(chosen) < REVIEW_SIZE and any(candidates.values()):
            for lesson in source:
                options = candidates[lesson.id]
                if options and len(chosen) < REVIEW_SIZE:
                    chosen.append(options.pop(pick(f"{seed}:{lesson.id}:{round_number}", len(options))))
            round_number += 1
    return sorted(chosen, key=lambda t: (t.lesson, t.index))


# ------------------------------------------------------------------- keys

CONTROL_NAMES = {0x00: "<C-@>", 0x08: "<BS>", 0x09: "<Tab>", 0x0A: "<NL>", 0x0D: "<CR>", 0x1B: "<Esc>",
                 0x1C: "<C-\\>", 0x1D: "<C-]>", 0x1E: "<C-^>", 0x1F: "<C-_>", 0x7F: "<BS>"}
SPECIAL_KEYS = {b"ku": "<Up>", b"kd": "<Down>", b"kl": "<Left>", b"kr": "<Right>", b"kb": "<BS>",
                b"kD": "<Del>", b"kh": "<Home>", b"@7": "<End>", b"kP": "<PageUp>", b"kN": "<PageDown>"}
NOTATION_ALIASES = {"esc": "<Esc>", "cr": "<CR>", "enter": "<CR>", "return": "<CR>", "tab": "<Tab>",
                    "bs": "<BS>", "space": " ", "lt": "<lt>", "bar": "|", "nl": "<NL>", "del": "<Del>",
                    "up": "<Up>", "down": "<Down>", "left": "<Left>", "right": "<Right>",
                    "c-i": "<Tab>", "c-m": "<CR>", "c-[": "<Esc>", "c-h": "<BS>", "c-j": "<NL>"}
TOKEN_BYTES = {"<Esc>": b"\x1b", "<CR>": b"\r", "<Tab>": b"\t", "<BS>": b"\x7f", "<NL>": b"\n", "<lt>": b"<",
               "<Up>": b"\x1bOA", "<Down>": b"\x1bOB", "<Right>": b"\x1bOC", "<Left>": b"\x1bOD",
               "<Del>": b"\x1b[3~", "<C-@>": b"\x00"}


def control_token(byte: int) -> str:
    if byte in CONTROL_NAMES:
        return CONTROL_NAMES[byte]
    return f"<C-{chr(byte + 0x60)}>"


def notation_tokens(notation: str) -> List[str]:
    """Split vim key notation ('ciwfoo<Esc>') into one token per keypress."""
    tokens: List[str] = []
    i = 0
    while i < len(notation):
        if notation[i] == "<":
            end = notation.find(">", i)
            name = notation[i + 1:end].lower() if end != -1 else ""
            if name in NOTATION_ALIASES:
                tokens.append(NOTATION_ALIASES[name])
                i = end + 1
                continue
            if re.fullmatch(r"c-[a-z\\\]^_@]", name):
                tokens.append(control_token(ord(name[2]) & 0x1F))
                i = end + 1
                continue
            tokens.append("<lt>")
            i += 1
            continue
        tokens.append(notation[i])
        i += 1
    return tokens


def tokens_to_bytes(tokens: List[str]) -> bytes:
    out = bytearray()
    for token in tokens:
        if token in TOKEN_BYTES:
            out += TOKEN_BYTES[token]
        elif token.startswith("<C-") and len(token) == 5:
            out.append(ord(token[3]) & 0x1F)
        else:
            out += token.encode("utf-8")
    return bytes(out)


def parse_keylog(data: bytes) -> List[str]:
    """Turn the file vim writes with -W into one token per keypress."""
    tokens: List[str] = []
    pending = bytearray()
    ctrl = False
    i = 0
    while i < len(data):
        byte = data[i]
        if byte == 0x80 and i + 2 < len(data):
            pair = bytes(data[i + 1:i + 3])
            i += 3
            if pair == b"\xfeX":
                pending.append(0x80)
            elif pair[0] == 0xFC:
                ctrl = bool(pair[1] & 0x04)
                continue
            elif pair in SPECIAL_KEYS:
                tokens.append(SPECIAL_KEYS[pair])
            else:
                continue
        else:
            i += 1
            if byte >= 0x80:
                pending.append(byte)
            elif byte < 0x20 or byte == 0x7F:
                tokens.append(control_token(byte))
            elif ctrl and chr(byte).isalpha():
                tokens.append(control_token(byte & 0x1F))
            elif byte == 0x3C:
                tokens.append("<lt>")
            else:
                tokens.append(chr(byte))
            ctrl = False
        if pending:
            try:
                tokens.append(pending.decode("utf-8"))
                pending.clear()
            except UnicodeDecodeError:
                if len(pending) >= 4:
                    pending.clear()
    return tokens


# --------------------------------------------------------------- progress


@dataclass
class State:
    start: Optional[dt.date] = None
    done: Dict[str, Tuple[str, dt.date]] = field(default_factory=dict)
    reviews: Set[dt.date] = field(default_factory=set)

    @property
    def passed(self) -> Set[str]:
        return {lesson for lesson, (event, _) in self.done.items() if event != "skip"}


def load_state() -> State:
    state = State()
    if not PROGRESS_FILE.exists():
        return state
    for line in PROGRESS_FILE.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            day = dt.date.fromisoformat(parts[0])
        except ValueError:
            continue
        event = parts[1]
        if event == "start":
            state.start = min(state.start, day) if state.start else day
        elif event == "review":
            state.reviews.add(day)
        elif event in ("pass", "override", "skip") and len(parts) > 2:
            previous = state.done.get(parts[2])
            if previous is None or (previous[0] == "skip" and event != "skip"):
                state.done[parts[2]] = (event, day)
    return state


def new_lesson_days(start: dt.date, today: dt.date) -> int:
    """Monday to Thursday dates from start through today."""
    count, day = 0, start
    while day <= today:
        if day.weekday() < 4:
            count += 1
        day += dt.timedelta(days=1)
    return count


def next_lesson(state: State, lessons: List[Lesson]) -> Optional[Lesson]:
    return next((l for l in lessons if l.id not in state.done), None)


def owed(state: State, lessons: List[Lesson], today: dt.date) -> int:
    remaining = sum(1 for l in lessons if l.id not in state.done)
    if not state.start:
        return min(1, remaining)
    return max(0, min(remaining, new_lesson_days(state.start, today) - len(state.passed)))


def plan(state: State, lessons: List[Lesson], today: dt.date) -> Optional[str]:
    """What today still calls for: 'lesson', 'review' or None."""
    upcoming = next_lesson(state, lessons)
    if not state.done and upcoming:
        return "lesson"
    weekday = today.weekday()
    if weekday < 5 and (weekday == 4 or upcoming is None) and state.done and today not in state.reviews:
        return "review"
    if upcoming and owed(state, lessons, today) > 0:
        return "lesson"
    return None


# -------------------------------------------------------------------- git


def git(*args: str, timeout: int = 25) -> Optional[subprocess.CompletedProcess]:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True,
                              timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return None


def has_repo() -> bool:
    return (ROOT / ".git").exists()


def has_remote() -> bool:
    result = git("remote")
    return bool(result and result.returncode == 0 and result.stdout.strip())


def sync_pull(quiet: bool = False) -> None:
    if not has_repo() or not has_remote():
        return
    result = git("pull", "--rebase", "--autostash", "--quiet")
    if result is None or result.returncode != 0:
        git("rebase", "--abort")
        if not quiet:
            print(dim("Could not pull progress from the remote; using what is on this machine."))


def record(event: str, *details: str) -> None:
    line = " ".join([dt.date.today().isoformat(), event, *details])
    with PROGRESS_FILE.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")
    if not has_repo():
        return
    git("add", "progress.log")
    git("commit", "--quiet", "-m", f"progress: {event} {' '.join(details[:1])}".strip(), "--", "progress.log")
    if not has_remote():
        return
    pushed = git("push", "--quiet")
    if pushed is None or pushed.returncode != 0:
        sync_pull(quiet=True)
        pushed = git("push", "--quiet")
    if pushed is None or pushed.returncode != 0:
        print(dim("Progress is saved locally; it will be pushed next time the remote is reachable."))


def ensure_started() -> None:
    if load_state().start is None:
        record("start")


# --------------------------------------------------------------- terminal


def use_color() -> bool:
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return False
    return not IS_WINDOWS or bool(os.environ.get("WT_SESSION"))


def paint(code: str, text: str) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if use_color() else text


def green(text: str) -> str:
    return paint("32;1", text)


def red(text: str) -> str:
    return paint("31;1", text)


def dim(text: str) -> str:
    return paint("2", text)


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return "q"


def find_vim() -> Optional[str]:
    found = shutil.which("vim")
    if found or not IS_WINDOWS:
        return found
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(variable)
        if base:
            matches = sorted(Path(base).glob("Vim/vim*/vim.exe"))
            if matches:
                return str(matches[-1])
    return None


def require_vim() -> str:
    vim = find_vim()
    if vim:
        return vim
    hint = "winget install vim.vim" if IS_WINDOWS else ("brew install vim" if IS_MAC else "sudo apt install vim")
    sys.exit(f"vim was not found on PATH. Install it first: {hint}")


# --------------------------------------------------------------- sessions


@dataclass
class Session:
    heading: str
    intro: List[str]
    tasks: List[Task]
    review_uids: Set[str]
    ext: str
    opts: str = ""
    arrows: bool = False
    require: List[Tuple[str, str]] = field(default_factory=list)
    vimrc: List[Tuple[str, str]] = field(default_factory=list)
    kind: str = "edit"


def lesson_session(lesson: Lesson, lessons: List[Lesson]) -> Session:
    tasks, review_uids = lesson_tasks(lesson, lessons)
    intro = ([f"Keys: {lesson.keys}", ""] if lesson.keys else []) + lesson.body
    return Session(heading=f"LESSON {lesson.num} of {len(lessons)} · {lesson.title}", intro=intro, tasks=tasks,
                   review_uids=review_uids, ext=lesson.ext, opts=lesson.opts, arrows=lesson.arrows,
                   require=lesson.require, vimrc=lesson.vimrc, kind=lesson.kind)


def review_session(state: State, lessons: List[Lesson], today: dt.date) -> Optional[Session]:
    done = [l for l in lessons if l.id in state.done and l.kind == "edit"]
    last_review = max(state.reviews) if state.reviews else None
    fresh = [l for l in done if last_review is None or state.done[l.id][1] > last_review]
    pool = fresh or done
    tasks = review_tasks(pool, done, today.isoformat())
    if not tasks:
        return None
    intro = ["No new concept today. These tasks reuse what you have already covered:", ""]
    intro += [f"  {l.num:>2}  {l.title}:  {l.keys}" for l in pool]
    return Session(heading=f"REVIEW · {today.strftime('%A %d %B')}", intro=intro, tasks=tasks,
                   review_uids=set(), ext="ts")


def lesson_pane(session: Session, multi_file: bool) -> List[str]:
    """The lesson text. A {L+n} in task k becomes {@k+n}, which session.vim keeps resolved to a live line number."""
    lines = [session.heading, ""] + session.intro + [""]
    if session.kind == "vimrc":
        lines.append("TASKS · this session edits your real vimrc (a backup was just made next to it).")
    else:
        lines.append("TASKS · each has a block marked [n] below. Don't edit the marker lines.")
    for number, task in enumerate(session.tasks, 1):
        tag = "(review) " if task.uid in session.review_uids else ""
        text = [LINE_REF_RE.sub(lambda m: "{@%d+%d}" % (number, int(m.group(1) or 0)), row) for row in task.text]
        lines.append(f" [{number}] {tag}{text[0]}")
        lines.extend(f"     {extra}" for extra in text[1:])
    lines.append("")
    finish = ":wqa  (saves every file and quits)" if multi_file else ":wq"
    lines.append(f"Finish: {finish}     Abandon: :qa!")
    return lines


@dataclass
class Report:
    passed: bool
    lines: List[str]


def diff_lines(expected: List[str], actual: List[str]) -> List[str]:
    out = []
    for line in difflib.unified_diff(expected, actual, lineterm="", n=1):
        if line.startswith(("---", "+++", "@@")):
            continue
        out.append("        " + (green(line) if line.startswith("+") else red(line) if line.startswith("-") else line))
    return out[:12]


def check_files(session: Session, targets: Dict[str, dict], work: Path) -> Tuple[bool, List[str]]:
    expected_files, _ = compose(session.tasks, session.ext, targets)
    main = main_name(session.ext)

    def read(name: str) -> List[str]:
        path = work / name
        if not path.exists():
            return []
        return normalize(path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").split("\n"))

    actual_blocks = split_blocks(read(main))
    expected_blocks = split_blocks(expected_files[main])
    lines: List[str] = []
    all_ok = True
    for number, task in enumerate(session.tasks, 1):
        problems: List[str] = []
        if task.main is not None:
            if number not in actual_blocks:
                problems.append(f"        the marker line for [{number}] is gone")
            elif actual_blocks[number] != expected_blocks[number]:
                problems += diff_lines(expected_blocks[number], actual_blocks[number])
        for name in task.files:
            actual = read(name)
            if actual != normalize(expected_files[name]):
                problems += [f"        in {name}:"] + diff_lines(normalize(expected_files[name]), actual)
        all_ok = all_ok and not problems
        lines.append(f"  [{number}] " + (green("ok") if not problems else red("not yet")))
        lines += problems
    if any(l.startswith("        ") for l in lines):
        lines.append(dim("        (- is the expected line, + is yours)"))
    return all_ok, lines


def check_keys(session: Session, tokens: List[str]) -> Tuple[bool, List[str]]:
    typed = "".join(tokens)
    missing = [label for regex, label in session.require if not re.search(regex, typed)]
    if not missing:
        return True, []
    return False, ["  " + red("never used: ") + ", ".join(missing)]


def stale_tasks(session: Session, targets: Dict[str, dict]) -> List[str]:
    return [t.uid for t in session.tasks
            if t.uid not in targets or targets[t.uid]["hash"] != t.digest(lesson_opts(t.lesson))]


_OPTS_BY_LESSON: Dict[str, str] = {}


def lesson_opts(lesson_id: str) -> str:
    if not _OPTS_BY_LESSON:
        _OPTS_BY_LESSON.update({l.id: l.opts for l in load_lessons()})
    return _OPTS_BY_LESSON.get(lesson_id, "")


def find_vimrc() -> Path:
    home = Path.home()
    candidates = [home / ".vimrc", home / ".vim" / "vimrc", home / "_vimrc", home / "vimfiles" / "vimrc"]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return home / ("_vimrc" if IS_WINDOWS else ".vimrc")


def launch_vim(vim: str, cwd: Path, target: str, pane: Path, keylog: Path, session: Session, line: int) -> None:
    config = {"lesson": pane.as_posix(), "line": line, "arrows": int(session.arrows), "opts": session.opts}
    command = [vim, "-N", "-W", str(keylog), "--cmd", "let g:vimdaily = " + json.dumps(config),
               "-S", str(SESSION_VIM), target]
    subprocess.call(command, cwd=str(cwd))


def attempt(session: Session, targets: Dict[str, dict], vim: str) -> Report:
    tmp = Path(tempfile.mkdtemp(prefix="vimdaily-"))
    try:
        keylog = tmp / "keys.log"
        pane = tmp / "lesson.txt"
        if session.kind == "vimrc":
            vimrc = find_vimrc()
            if vimrc.exists():
                shutil.copy2(vimrc, vimrc.with_name(vimrc.name + f".vimdaily-backup-{dt.date.today():%Y%m%d}"))
            pane.write_text("\n".join(lesson_pane(session, False)) + "\n", encoding="utf-8")
            launch_vim(vim, vimrc.parent, str(vimrc), pane, keylog, session, 1)
            content = vimrc.read_text(encoding="utf-8", errors="replace") if vimrc.exists() else ""
            missing = [label for regex, label in session.vimrc if not re.search(regex, content, re.MULTILINE)]
            files_ok = not missing
            lines = ["  " + red("not in your vimrc yet: ") + label for label in missing]
            reference = 0
        else:
            work = tmp / "work"
            work.mkdir()
            files, body_line = compose(session.tasks, session.ext)
            for name, content in files.items():
                with (work / name).open("w", encoding="utf-8", newline="\n") as handle:
                    handle.write("\n".join(content) + "\n")
            pane.write_text("\n".join(lesson_pane(session, len(files) > 1)) + "\n", encoding="utf-8")
            launch_vim(vim, work, main_name(session.ext), pane, keylog, session, min(body_line.values(), default=1))
            files_ok, lines = check_files(session, targets, work)
            reference = sum(targets[t.uid]["keys"] for t in session.tasks)
        tokens = parse_keylog(keylog.read_bytes()) if keylog.exists() else []
        keys_ok, key_lines = check_keys(session, tokens)
        passed = files_ok and keys_ok
        summary = green("PASS") if passed else red("NOT PASSED")
        counts = f"{len(tokens)} keys"
        if reference:
            counts += f" (reference solution: {reference}, plus moving between tasks)"
        return Report(passed, [f"{summary}  {counts}"] + lines + key_lines)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_session(session: Session, targets: Dict[str, dict]) -> Optional[str]:
    """Run until passed, overridden or abandoned. Returns 'pass', 'override' or None."""
    vim = require_vim()
    if session.kind == "edit":
        stale = stale_tasks(session, targets)
        if stale:
            sys.exit("Lesson files changed since targets.json was built (tasks " + ", ".join(stale) +
                     ").\nRebuild on macOS or Linux with: python3 tools/build.py")
    while True:
        report = attempt(session, targets, vim)
        print()
        print("\n".join(report.lines))
        if report.passed:
            return "pass"
        choice = ask("\n[r]etry   [o]verride (count it as passed)   [q]uit for now: ")
        if choice.startswith("o"):
            return "override"
        if not choice.startswith("r"):
            return None


def resize_terminal() -> None:
    if IS_WINDOWS:
        os.system("mode con: cols=104 lines=50 >nul 2>&1")
    elif sys.stdout.isatty():
        sys.stdout.write("\x1b[8;50;104t")
        sys.stdout.flush()
        time.sleep(0.15)


# --------------------------------------------------------------- commands


def describe_idle(state: State, lessons: List[Lesson], today: dt.date) -> str:
    upcoming = next_lesson(state, lessons)
    if upcoming is None:
        return "Curriculum complete. Review sessions run on weekdays; `vimdaily practice N` repeats any lesson."
    when = "Monday" if today.weekday() >= 3 else "tomorrow"
    return (f"Nothing due right now. Next: lesson {upcoming.num}, {upcoming.title}, on {when}.\n"
            "`vimdaily practice N` repeats a lesson; `vimdaily skip` marks the next one as already known.")


def cmd_run(args: argparse.Namespace) -> None:
    if args.window:
        resize_terminal()
    sync_pull()
    ensure_started()
    lessons, targets = load_lessons(), load_targets()
    today = dt.date.today()
    first = True
    while True:
        state = load_state()
        what = plan(state, lessons, today)
        if what is None:
            if first:
                print(describe_idle(state, lessons, today))
            break
        if what == "lesson":
            lesson = next_lesson(state, lessons)
            assert lesson is not None
            if not first:
                behind = owed(state, lessons, today)
                answer = ask(f"\nYou are {behind} lesson(s) behind schedule. Take lesson {lesson.num} "
                             f"({lesson.title}) now? [y/N] ")
                if not answer.startswith("y"):
                    break
            outcome = run_session(lesson_session(lesson, lessons), targets)
            if outcome is None:
                print(f"Lesson {lesson.num} stays open. Run `vimdaily` to try again.")
                break
            record(outcome, lesson.id)
        else:
            session = review_session(state, lessons, today)
            if session is None:
                record("review")
                continue
            outcome = run_session(session, targets)
            if outcome is None:
                print("The review stays open. Run `vimdaily` to try again.")
                break
            record("review")
        first = False
    if args.window:
        ask("\nPress Enter to close. ")


def cmd_skip(_: argparse.Namespace) -> None:
    sync_pull()
    ensure_started()
    lessons = load_lessons()
    lesson = next_lesson(load_state(), lessons)
    if lesson is None:
        print("Nothing left to skip.")
        return
    record("skip", lesson.id)
    following = next_lesson(load_state(), lessons)
    print(f"Skipped lesson {lesson.num} ({lesson.title}).")
    if following:
        print(f"Next up: lesson {following.num} ({following.title}). Run `vimdaily` to take it.")


def cmd_status(_: argparse.Namespace) -> None:
    sync_pull(quiet=True)
    lessons, state, today = load_lessons(), load_state(), dt.date.today()
    skipped = len(state.done) - len(state.passed)
    print(f"{len(state.passed)} passed, {skipped} skipped, {len(lessons) - len(state.done)} to go "
          f"({len(state.reviews)} review sessions).")
    upcoming = next_lesson(state, lessons)
    if upcoming:
        print(f"Next lesson: {upcoming.num}, {upcoming.title}")
    due = plan(state, lessons, today)
    print({"lesson": "Due now: a lesson. Run `vimdaily`.", "review": "Due now: the weekly review. Run `vimdaily`.",
           None: "Nothing due right now."}[due])
    for lesson in lessons:
        mark = {"pass": "x", "override": "o", "skip": "-"}.get(state.done.get(lesson.id, ("", None))[0], " ")
        print(f"  [{mark}] {lesson.num:>2}  {lesson.title}")


def cmd_practice(args: argparse.Namespace) -> None:
    lessons, targets = load_lessons(), load_targets()
    if args.what == "review":
        session = review_session(load_state(), lessons, dt.date.today())
        if session is None:
            sys.exit("Nothing to review yet.")
    else:
        matches = [l for l in lessons if args.what.isdigit() and l.num == int(args.what)]
        if not matches:
            sys.exit(f"No lesson {args.what!r}. Use a number from 1 to {len(lessons)}, or 'review'.")
        session = lesson_session(matches[0], lessons)
        session.heading = "PRACTICE · " + session.heading
    run_session(session, targets)
    print(dim("Practice runs are not recorded."))


# ------------------------------------------------------------- scheduling


def config() -> dict:
    defaults = {"time": "08:30"}
    if CONFIG_FILE.exists():
        defaults.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    return defaults


def scheduled_time() -> dt.time:
    hour, minute = config()["time"].split(":")
    return dt.time(int(hour), int(minute))


def cache_dir() -> Path:
    if IS_WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    path = base / "vimdaily"
    path.mkdir(parents=True, exist_ok=True)
    return path


def console_python() -> str:
    if IS_MAC and Path("/usr/bin/python3").exists():
        return "/usr/bin/python3"
    executable = Path(sys.executable)
    if IS_WINDOWS and executable.name.lower() == "pythonw.exe":
        return str(executable.with_name("python.exe"))
    return str(executable)


def open_window() -> None:
    python, script = console_python(), str(ROOT / "vimdaily.py")
    if IS_WINDOWS:
        subprocess.Popen([python, script, "run", "--window"], creationflags=subprocess.CREATE_NEW_CONSOLE)
    elif IS_MAC:
        launcher = cache_dir() / "vim-daily.command"
        launcher.write_text(f'#!/bin/sh\nexec "{python}" "{script}" run --window\n', encoding="utf-8")
        launcher.chmod(0o755)
        subprocess.Popen(["open", "-a", "Terminal", str(launcher)])
    else:
        terminal = shutil.which("x-terminal-emulator") or shutil.which("gnome-terminal") or shutil.which("xterm")
        if terminal is None:
            sys.exit("No terminal emulator found (looked for x-terminal-emulator, gnome-terminal, xterm).")
        separator = "--" if terminal.endswith("gnome-terminal") else "-e"
        subprocess.Popen([terminal, separator, python, script, "run", "--window"], start_new_session=True)


def cmd_scheduled(args: argparse.Namespace) -> None:
    now = dt.datetime.now()
    today = now.date()
    stamp = cache_dir() / "last-popup"
    if not args.force:
        if today.weekday() >= 5 or now.time() < scheduled_time():
            return
        if stamp.exists() and stamp.read_text(encoding="utf-8").strip() == today.isoformat():
            return
        if not IS_WINDOWS and not IS_MAC and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            return
    sync_pull(quiet=True)
    stamp.write_text(today.isoformat() + "\n", encoding="utf-8")
    if args.force or plan(load_state(), load_lessons(), today) is not None:
        open_window()


def launchd_plist(hour: int, minute: int) -> str:
    slots = "".join(
        f"<dict><key>Weekday</key><integer>{day}</integer><key>Hour</key><integer>{hour}</integer>"
        f"<key>Minute</key><integer>{minute}</integer></dict>" for day in range(1, 6))
    log = cache_dir() / "scheduled.log"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>{LAUNCHD_LABEL}</string>
<key>ProgramArguments</key><array><string>{console_python()}</string><string>{ROOT / 'vimdaily.py'}</string><string>scheduled</string></array>
<key>StartCalendarInterval</key><array>{slots}</array>
<key>RunAtLoad</key><true/>
<key>StandardOutPath</key><string>{log}</string>
<key>StandardErrorPath</key><string>{log}</string>
</dict></plist>
"""


def run_quiet(command: List[str]) -> bool:
    try:
        return subprocess.run(command, capture_output=True, text=True).returncode == 0
    except OSError:
        return False


def install_scheduler() -> str:
    when = scheduled_time()
    python, script = console_python(), str(ROOT / "vimdaily.py")
    if IS_MAC:
        plist = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
        plist.parent.mkdir(parents=True, exist_ok=True)
        domain = f"gui/{os.getuid()}"
        run_quiet(["launchctl", "bootout", f"{domain}/{LAUNCHD_LABEL}"])
        plist.write_text(launchd_plist(when.hour, when.minute), encoding="utf-8")
        if not run_quiet(["launchctl", "bootstrap", domain, str(plist)]):
            run_quiet(["launchctl", "load", "-w", str(plist)])
        return f"launchd agent {plist}"
    if IS_WINDOWS:
        pythonw = Path(python).with_name("pythonw.exe")
        runner = str(pythonw if pythonw.exists() else python)
        ps = (f"$a = New-ScheduledTaskAction -Execute '{runner}' -Argument '\"{script}\" scheduled'; "
              f"$t = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday "
              f"-At {when:%H:%M}; "
              "$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries "
              "-DontStopIfGoingOnBatteries; "
              f"Register-ScheduledTask -TaskName {TASK_NAME} -Action $a -Trigger $t -Settings $s -Force | Out-Null")
        if not run_quiet(["powershell", "-NoProfile", "-Command", ps]):
            sys.exit("Could not register the scheduled task with PowerShell.")
        return f"scheduled task {TASK_NAME}"
    units = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "systemd" / "user"
    units.mkdir(parents=True, exist_ok=True)
    (units / "vimdaily.service").write_text(
        "[Unit]\nDescription=vim-daily: open today's lesson if one is due\n\n"
        f"[Service]\nType=oneshot\nKillMode=process\nExecStart={python} {script} scheduled\n", encoding="utf-8")
    # Polls rather than firing once: a desktop session may not exist yet at 08:30,
    # and `scheduled` opens at most one window a day.
    (units / "vimdaily.timer").write_text(
        "[Unit]\nDescription=vim-daily weekday check\n\n"
        "[Timer]\nOnCalendar=Mon..Fri *-*-* 6..20:0/10:00\nPersistent=true\n\n"
        "[Install]\nWantedBy=timers.target\n", encoding="utf-8")
    run_quiet(["systemctl", "--user", "daemon-reload"])
    if not run_quiet(["systemctl", "--user", "enable", "--now", "vimdaily.timer"]):
        sys.exit(f"Wrote the units to {units} but `systemctl --user enable --now vimdaily.timer` failed.")
    return f"systemd user timer {units / 'vimdaily.timer'}"


def uninstall_scheduler() -> None:
    if IS_MAC:
        plist = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
        run_quiet(["launchctl", "bootout", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"])
        plist.unlink(missing_ok=True)
    elif IS_WINDOWS:
        run_quiet(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
    else:
        units = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "systemd" / "user"
        run_quiet(["systemctl", "--user", "disable", "--now", "vimdaily.timer"])
        for name in ("vimdaily.service", "vimdaily.timer"):
            (units / name).unlink(missing_ok=True)
        run_quiet(["systemctl", "--user", "daemon-reload"])


def command_shim() -> Path:
    if IS_WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Microsoft" / "WindowsApps" / "vimdaily.cmd"
    return Path.home() / ".local" / "bin" / "vimdaily"


def use_noreply_identity() -> None:
    """Progress commits go to a public repository, so prefer GitHub's no-reply address for them."""
    if not has_repo() or not shutil.which("gh"):
        return
    current = git("config", "--local", "user.email")
    if current and current.stdout.strip():
        return
    try:
        result = subprocess.run(["gh", "api", "user", "--jq", r'"\(.id)+\(.login)@users.noreply.github.com"'],
                                capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return
    address = result.stdout.strip()
    if result.returncode == 0 and address.endswith("@users.noreply.github.com"):
        git("config", "--local", "user.email", address)


def cmd_install(_: argparse.Namespace) -> None:
    require_vim()
    use_noreply_identity()
    shim = command_shim()
    shim.parent.mkdir(parents=True, exist_ok=True)
    if IS_WINDOWS:
        shim.write_text(f'@"{console_python()}" "{ROOT / "vimdaily.py"}" %*\r\n', encoding="utf-8")
    else:
        (ROOT / "vimdaily.py").chmod(0o755)
        if shim.is_symlink() or shim.exists():
            shim.unlink()
        shim.symlink_to(ROOT / "vimdaily.py")
    print(f"Command: {shim}")
    print(f"Schedule: weekdays at {config()['time']} via {install_scheduler()}")


def cmd_uninstall(_: argparse.Namespace) -> None:
    uninstall_scheduler()
    shim = command_shim()
    if shim.is_symlink() or shim.exists():
        shim.unlink()
    print("Removed the schedule and the vimdaily command. The repository and progress.log are untouched.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="vimdaily", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="take whatever is due today (the default)")
    run.add_argument("--window", action="store_true", help=argparse.SUPPRESS)
    sub.add_parser("skip", help="mark the next lesson as already known")
    sub.add_parser("status", help="show progress")
    practice = sub.add_parser("practice", help="repeat a lesson or a review without recording it")
    practice.add_argument("what", help="lesson number, or 'review'")
    scheduled = sub.add_parser("scheduled", help="open a terminal window if something is due (used by the scheduler)")
    scheduled.add_argument("--force", action="store_true", help="open the window now, whatever the day or time")
    sub.add_parser("install", help="add the vimdaily command and the weekday schedule on this machine")
    sub.add_parser("uninstall", help="remove the command and the schedule")
    args = parser.parse_args()
    handlers = {"run": cmd_run, "skip": cmd_skip, "status": cmd_status, "practice": cmd_practice,
                "scheduled": cmd_scheduled, "install": cmd_install, "uninstall": cmd_uninstall}
    if args.command is None:
        args = parser.parse_args(["run"])
    handlers[args.command](args)


if __name__ == "__main__":
    main()
