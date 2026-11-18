#!/usr/bin/env python3
"""Play every lesson the way `vimdaily` serves it and confirm it can be passed.

    python3 tools/selftest.py            every lesson, plus sample review sessions
    python3 tools/selftest.py 7 12       lessons 7 and 12 only

Unlike tools/build.py this loads your own vimrc and plugins and uses the real
session layout (the lesson's tasks mixed with tasks from earlier lessons), so it
catches a vimrc setting or a neighbouring block that changes what the keys do.
macOS and Linux only.
"""
from __future__ import annotations

import datetime as dt
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build  # noqa: E402
import vimdaily as vd  # noqa: E402


def session_tokens(session: vd.Session, body_line: dict, main: str) -> List[str]:
    """Reference keys for every task, lowest block first so line numbers above stay valid."""
    has_files = any(task.files for task in session.tasks)
    numbered = list(enumerate(session.tasks, 1))
    in_main = sorted((item for item in numbered if item[1].main is not None), key=lambda item: -body_line[item[0]])
    elsewhere = [item for item in numbered if item[1].main is None]
    tokens: List[str] = []
    for number, task in in_main + elsewhere:
        if has_files:
            tokens += vd.notation_tokens(f":wa<CR>:only<CR>:Lesson<CR>:e {main}<CR>")
        start = body_line.get(number, min(body_line.values(), default=1))
        tokens += vd.notation_tokens(f":{start}<CR>0")
        tokens += vd.notation_tokens(vd.resolve_lines(task.keys, start))
    return tokens


def play(vim: str, name: str, session: vd.Session, lesson: vd.Lesson, targets: dict) -> Optional[str]:
    main = vd.main_name(session.ext)
    files, body_line = vd.compose(session.tasks, session.ext)
    tokens = session_tokens(session, body_line, main)
    try:
        result, typed = build.replay(vim, files, main, tokens, min(body_line.values(), default=1), lesson, clean=False)
    except build.ReplayError as error:
        return f"{name}: {error}"
    with tempfile.TemporaryDirectory(prefix="vimdaily-selftest-") as tmp:
        work = Path(tmp)
        for file_name, content in result.items():
            (work / file_name).write_text("\n".join(content) + "\n", encoding="utf-8")
        files_ok, lines = vd.check_files(session, targets, work)
    keys_ok, key_lines = vd.check_keys(session, typed)
    if not lesson.verify:
        keys_ok, key_lines = True, []
    if files_ok and keys_ok:
        return None
    return f"{name}:\n" + "\n".join(lines + key_lines)


def main() -> None:
    wanted = {int(arg) for arg in sys.argv[1:]}
    vim = vd.require_vim()
    lessons, targets = vd.load_lessons(), vd.load_targets()
    edit_lessons = [l for l in lessons if l.kind == "edit"]
    jobs: List[Tuple[str, vd.Session, vd.Lesson]] = []
    for lesson in edit_lessons:
        if not wanted or lesson.num in wanted:
            jobs.append((f"lesson {lesson.id}", vd.lesson_session(lesson, lessons), lesson))
    if not wanted:
        day = dt.date(2026, 1, 2)
        for upto in range(2, len(lessons) + 1, 4):
            state = vd.State(start=day, done={l.id: ("pass", day) for l in lessons[:upto]})
            if upto > 6:
                state.reviews.add(day)
                state.done.update({l.id: ("pass", day + dt.timedelta(days=3)) for l in lessons[upto - 4:upto]})
            session = vd.review_session(state, lessons, day + dt.timedelta(days=7 * upto))
            if session is not None:
                jobs.append((f"review after lesson {upto}", session, edit_lessons[0]))
    stale = sorted({uid for _, session, _ in jobs for uid in vd.stale_tasks(session, targets)})
    if stale:
        sys.exit("targets.json is out of date for " + ", ".join(stale) + "; run tools/build.py first.")
    with ThreadPoolExecutor(max_workers=4) as pool:
        failures = [f for f in pool.map(lambda job: play(vim, job[0], job[1], job[2], targets), jobs) if f]
    print(f"{len(jobs) - len(failures)} of {len(jobs)} sessions pass with the reference keys.")
    for failure in failures:
        print("FAILED " + failure)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
