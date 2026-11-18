# Lesson file format

One lesson per file, `NN-slug.txt`, served in numeric order. A lesson is a
header, an explanation, and three to five tasks. After editing any lesson, run
`python3 tools/build.py` (macOS or Linux) to regenerate `targets.json`;
`vimdaily` refuses to serve a lesson whose targets are out of date.

```
title: Operator + motion: d
keys: d{motion}  dw  de  d$  D
require: d\d*w :: dw
require: D|d\$ :: D or d$
ext: ts

d is an operator: it waits for a motion, then deletes whatever the motion
crosses. ...

== Put the cursor on "legacy" and delete that word with dw.
const legacy cache = new Map<string, User>();
-- w
-- dw
```

## Header

Lines of `key: value` up to the first blank line.

| key | meaning |
| --- | --- |
| `title` | shown as `LESSON 7 of 38 · <title>` |
| `keys` | the cheat line under the title, and in Friday reviews |
| `require` | `regex :: label`. One per line, all must match the keystrokes typed in the session, or the lesson is not passed. The label is what the learner sees in "never used: ..." |
| `ext` | `ts` (default) or `java`: language of the practice file |
| `opts` | extra `setlocal` arguments for the session, e.g. `cindent` |
| `arrows` | `yes` leaves the arrow keys on (lesson 1 only) |
| `verify` | `no` skips the build check that a reference solution uses every required key (for keys the build cannot replay, such as plugin commands) |
| `kind` | `vimrc` for a lesson that edits the real vimrc; see below |

## Explanation

Everything between the header and the first `== ` line. It is shown in a
read-only pane above the practice file that cannot be scrolled in the early
lessons, so keep it to **12 lines of at most 78 columns**. Say what the keys
do and when to reach for them. No history, no jokes.

## Tasks

```
== Instruction, at most 78 columns.
== An optional second instruction line.
@ flag
snippet line
snippet line
-- reference keys
-- more reference keys (the lines are concatenated)
```

- **Instruction**: what to achieve and with which key. The learner sees only
  this text and the snippet.
- **Snippet**: becomes a block in the practice file, under a marker line such
  as `// [3]`. Synthetic TypeScript or Java that looks like everyday
  application code. Keep each snippet under about 10 lines (scrolling lessons
  excepted). A snippet line must not start with `== `, `-- ` or `@ `.
- **Reference keys**: one correct solution in vim notation. The build types
  them into a real vim; whatever comes out is the target the learner's file is
  compared with. They also give the "reference solution: N keys" figure, so
  prefer the solution the lesson is teaching over a clever shorter one.

### Reference key notation

Plain characters are typed as they are. Special keys: `<Esc>` `<CR>` `<Tab>`
`<BS>` `<Space>` `<lt>` (a literal `<`) `<Bar>` and `<C-x>` for Ctrl-x.
Trailing spaces on a `-- ` line are fragile; write `<Space>`. Do not end with
`:wq`; the build appends `:wqa<CR>` itself.

`{L}` is replaced with the file line number of the snippet's first line, and
`{L+2}` with the line two below it. Use it in instructions and keys alike
whenever a task needs a line number (`{L+2}G`, `:{L},{L+3}s/a/b/`), because
the block's position in the file changes from session to session.

### Rules the build enforces

1. The keys start with the cursor in NORMAL mode on **column 1 of the
   snippet's first line**.
2. The keys must change the text, and must leave vim able to accept `:wqa`
   (back in NORMAL mode, no pending prompt).
3. The keys must affect **only their own block**. Each task is replayed a
   second time between two filler blocks, and the result must be the same.
   So no `:%s`, no `G`, no `dG`, no `gg=G`, no `:g` without a range. Use a
   `{L}` range, a visual selection, or a separate file (below) instead.
4. Blocks are separated by one blank line and each has a marker line directly
   above its first line. Paragraph motions and objects (`{`, `}`, `ip`, `ap`)
   see the marker as part of the snippet's first paragraph.

### Session settings

Every session forces these, so the same keys give the same text everywhere:
`autoindent`, no `smartindent`/`cindent`/`indentexpr` (unless `opts` says
otherwise), `expandtab shiftwidth=2 softtabstop=2`, empty `formatoptions`,
`iskeyword=@,48-57,_,192-255`, `nrformats=bin,hex`, `noignorecase`,
`wrapscan`, `nojoinspaces`, `startofline`. Arrow keys and the mouse are off.

### Flags

| flag | meaning |
| --- | --- |
| `@ first` | the block must be the first in the file (only allowed on a lesson's first task). For `gg`. |
| `@ last` | the block is placed last in the file. For `G`. |
| `@ file: name.ts` | the lines that follow go into a separate file instead of the practice file |
| `@ whole` | skip the between-filler-blocks replay |
| `@ noedit` | the keys are allowed to leave the text unchanged |

Tasks flagged `first` or `last` are never reused in other lessons or reviews.

### Tasks in other files

```
== Open orders.ts with :e and rename every "qty" to "quantity" using :%s.
@ file: orders.ts
export type Order = { id: string; qty: number };
-- :e orders.ts<CR>
-- :%s/qty/quantity/g<CR>
```

The file holds exactly the snippet, with no marker line, and belongs to that
one task, so whole-file commands are safe there. File names must be unique
across all lessons. The keys still start in the practice file, so they have to
open the file themselves. Only use this from lesson 17 on, which teaches `:e`.
A task may have snippet lines for the practice file, followed by one or more
`@ file:` sections.

## Required keys

`require` regexes are matched against the whole session's keystrokes written
as one string: plain characters as typed, special keys as `<Esc>`, `<CR>`,
`<Tab>`, `<BS>`, `<C-r>`, and a literal `<` as `<lt>`. Ctrl-i is `<Tab>`,
Ctrl-m is `<CR>`, Ctrl-[ is `<Esc>`. The match cannot tell NORMAL mode from
typed text, so it is a heuristic: prefer patterns of two or more keys
(`ci[("]`, `d\d*w`) over single letters.

## vimrc lessons

`kind: vimrc` opens the learner's real vimrc instead of a practice file. Tasks
are instructions only (no snippet, no keys). Each header line
`vimrc: regex :: label` must match the saved vimrc for the lesson to pass.

## Checking your work

```
python3 tools/build.py --show 7        build lesson 7 and print each before/after
python3 tools/build.py --dry-run 7     validate without writing targets.json
python3 tools/build.py --check         is targets.json current?
vimdaily practice 7                    take the lesson without recording it
```
