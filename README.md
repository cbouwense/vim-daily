# vim-daily

One vim concept per weekday. At 08:30 a terminal window opens with vim already
running: the day's lesson in a read-only pane on top, a practice file below.
Do the tasks, `:wq`, and the result is checked.

```
┌──────────────────────────────────────────────┐
│ LESSON 7 of 38 · Operator + motion: d        │
│ Keys: d{motion}  dw  de  d$  D               │
│                                              │
│ d is an operator: it waits for a motion ...  │
│                                              │
│ TASKS                                        │
│  [1] Put the cursor on "legacy" and delete   │
│      that word with dw.                      │
│  [2] (review) Add the missing semicolons ... │
├──────────────────────────────────────────────┤
│ // [1]                                       │
│ const legacy cache = new Map<string, User>();│
│                                              │
│ // [2]                                       │
│ const a = 1                                  │
└──────────────────────────────────────────────┘
PASS  14 keys (reference solution: 11, plus moving between tasks)
```

## How it runs

- **Monday to Thursday**: one new concept, most frequently used first, plus one
  or two tasks from earlier lessons.
- **Friday**: a review session built from the lessons passed since the last
  review. No new concept.
- A lesson is **passed** when every task block matches its target and the
  keystroke log shows you used the keys the lesson teaches. Keystroke count is
  shown against a reference solution but never blocks a pass. If the check is
  wrong or you are stuck, choose `override`.
- The next lesson unlocks only after the previous one is passed. Miss a day and
  the same lesson is served again; once you pass it you are offered the next
  one straight away until you are back on schedule.
- Arrow keys and the mouse are switched off inside practice sessions (lesson 1
  excepted). Your own vimrc and plugins are loaded.

The "used the key" check reads vim's keystroke log as plain text and cannot
tell NORMAL-mode commands from typed text, so treat it as a nudge.

## Commands

```
vimdaily               take whatever is due today
vimdaily status        progress and the full lesson list
vimdaily skip          mark the next lesson as already known
vimdaily practice 7    repeat lesson 7 (not recorded)
vimdaily practice review
vimdaily install       add the command and the weekday schedule on this machine
vimdaily uninstall
```

## Install

Needs Python 3.9+, vim and git.

| | prerequisites | install |
| --- | --- | --- |
| macOS | built in | `python3 vimdaily.py install` |
| Debian | `sudo apt install vim python3 git` | `python3 vimdaily.py install` |
| Windows | `winget install vim.vim Python.Python.3 Git.Git` | `py vimdaily.py install` |

```
git clone https://github.com/cbouwense/vim-daily
cd vim-daily
python3 vimdaily.py install
```

`install` puts a `vimdaily` command on your PATH and registers the schedule:

- **macOS**: a launchd agent (`~/Library/LaunchAgents/com.vimdaily.lesson.plist`)
  that opens Terminal.app. If the Mac is asleep at 08:30 it fires on wake.
- **Debian**: a systemd user timer that opens `x-terminal-emulator` once a
  desktop session exists.
- **Windows**: a Task Scheduler task (`VimDaily`) that opens a console window;
  a missed start runs as soon as the machine is available.

A window opens at most once a day, and only when something is due. Change the
time in `config.json`, then run `install` again.

The Windows and Debian schedulers were written from documentation and have not
been run on real machines yet. The lessons themselves are verified on macOS
and in a Debian container.

## Progress across machines

`progress.log` is committed. `vimdaily` pulls before each session and commits
and pushes after each result, so any machine with a clone picks up where the
last one stopped. The log is append-only and merged with git's `union` driver,
so two machines cannot conflict. Offline, it uses the local log and pushes
next time.

## Lessons

Lessons are plain text in `lessons/`; the format is described in
[`lessons/FORMAT.md`](lessons/FORMAT.md). Each task carries reference keys, and
`tools/build.py` types them into a real vim to produce the targets in
`targets.json`, so a task cannot ask for something its own solution does not
produce. `tools/selftest.py` plays every lesson as it is served.

```
python3 tools/build.py        rebuild targets.json after editing a lesson
python3 tools/selftest.py     confirm every session can be passed
```
