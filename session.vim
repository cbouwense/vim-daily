" vim-daily practice session. Sourced with -S after the practice file is open.
" g:vimdaily = {lesson: path, line: N, arrows: 0|1, opts: 'setlocal args', ready: path}

let s:save_cpo = &cpo
set cpo&vim

let s:cfg = g:vimdaily
let s:lesson_lines = readfile(s:cfg.lesson)

" Results are compared against a fixed target, so the options that change what
" a keystroke does to the text must be the same on every machine.
set backspace=indent,eol,start nojoinspaces noignorecase nosmartcase wrapscan magic
set nogdefault startofline selection=inclusive virtualedit= whichwrap=b,s notildeop
set noshiftround nosmarttab noswapfile viminfo= laststatus=2 nosplitright nosplitbelow
set completeopt=menu,preview complete=.,w,b,u,t,i

" The wheel still scrolls (the lesson pane may be taller than its window), but
" clicking cannot stand in for a motion.
set mouse=a
for s:button in ['Left', 'Right', 'Middle']
  for s:event in ['Mouse', '2-Mouse', '3-Mouse', '4-Mouse', 'Drag', 'Release']
    let s:name = '<' . substitute(s:event, '\(\d-\)\?\(\a\+\)', '\1' . s:button . '\2', '') . '>'
    execute 'silent! noremap ' . s:name . ' <Nop>'
    execute 'silent! inoremap ' . s:name . ' <Nop>'
  endfor
endfor

function! s:Enforce() abort
  if &buftype !=# ''
    return
  endif
  setlocal autoindent nosmartindent nocindent indentexpr= formatoptions= textwidth=0 wrapmargin=0
  setlocal expandtab shiftwidth=2 softtabstop=2 tabstop=2
  setlocal iskeyword=@,48-57,_,192-255 matchpairs=(:),{:},[:] nrformats=bin,hex
  setlocal number norelativenumber
  if get(s:cfg, 'opts', '') !=# ''
    execute 'setlocal ' . s:cfg.opts
  endif
endfunction

function! s:LineOf(task, offset) abort
  return has_key(s:marks, a:task) ? s:marks[a:task] + 1 + a:offset : '?'
endfunction

" The lesson text holds {@task+offset} where a task names a line. They are
" resolved against the marker lines as they stand now, so the numbers stay
" right after an earlier task has added or removed lines.
function! s:Render() abort
  let s:marks = {}
  let l:number = 0
  for l:text in getbufline(s:main_buf, 1, '$')
    let l:number += 1
    let l:found = matchlist(l:text, '^\S\{1,2} \[\(\d\+\)\]\s*$')
    if !empty(l:found)
      let s:marks[l:found[1]] = l:number
    endif
  endfor
  let l:rows = []
  for l:row in s:lesson_lines
    call add(l:rows, substitute(l:row, '{@\(\d\+\)+\(\d\+\)}', '\=s:LineOf(submatch(1), submatch(2))', 'g'))
  endfor
  if join(l:rows, "\n") !=# join(getbufline(s:lesson_buf, 1, '$'), "\n")
    call setbufvar(s:lesson_buf, '&modifiable', 1)
    call setbufline(s:lesson_buf, 1, l:rows)
    call setbufvar(s:lesson_buf, '&modifiable', 0)
  endif
endfunction

function! s:LessonHeight() abort
  return min([len(s:lesson_lines), max([5, &lines - 14])])
endfunction

function! s:StyleLessonWindow() abort
  setlocal nonumber norelativenumber wrap linebreak breakindent winfixheight nocursorline nolist colorcolumn=
  let l:clipped = len(s:lesson_lines) > s:LessonHeight() ? '   (more below: scroll with the mouse wheel)' : ''
  let &l:statusline = ' vim-daily lesson' . l:clipped . '%=:Lesson reopens this pane '
endfunction

function! s:ShowLesson() abort
  if bufwinnr(s:lesson_buf) != -1
    return
  endif
  let l:back = win_getid()
  execute 'silent topleft ' . s:LessonHeight() . 'split'
  execute 'silent buffer ' . s:lesson_buf
  call s:StyleLessonWindow()
  call win_gotoid(l:back)
endfunction

function! s:CloseIfAlone() abort
  if winnr('$') != 1 || bufnr('%') != s:lesson_buf
    return
  endif
  let l:modified = filter(range(1, bufnr('$')), 'buflisted(v:val) && getbufvar(v:val, "&modified")')
  if empty(l:modified)
    qall
  else
    execute 'botright sbuffer ' . l:modified[0]
    echohl WarningMsg
    echo 'Unsaved changes here. :w saves this file, :wqa saves everything and finishes.'
    echohl None
  endif
endfunction

function! s:Resize() abort
  let l:win = bufwinid(s:lesson_buf)
  if l:win != -1
    call win_execute(l:win, 'resize ' . s:LessonHeight())
    call win_execute(l:win, 'call s:StyleLessonWindow()')
  endif
endfunction

call s:Enforce()
let s:practice_win = win_getid()
let s:main_buf = bufnr('%')

execute 'silent topleft ' . s:LessonHeight() . 'new'
setlocal buftype=nofile bufhidden=hide noswapfile nobuflisted
silent file vim-daily-lesson
let s:lesson_buf = bufnr('%')
call s:Render()
setlocal nomodifiable
call s:StyleLessonWindow()
syntax match Title /^\(LESSON\|REVIEW\|PRACTICE\) .*$/
syntax match Statement /^\(Keys\|TASKS\|Finish\)\>.*$/
syntax match Identifier /^ *\[\d\+\]/
syntax match Comment /(review)/

call win_gotoid(s:practice_win)
call cursor(s:cfg.line, 1)

if !get(s:cfg, 'arrows', 0)
  for s:key in ['<Up>', '<Down>', '<Left>', '<Right>']
    execute 'noremap ' . s:key . ' <Cmd>echo "Arrow keys are off in practice: use h j k l"<CR>'
    execute 'inoremap ' . s:key . ' <Cmd>echo "Arrow keys are off in practice: Esc, then h j k l"<CR>'
  endfor
endif

command! Lesson call s:ShowLesson()

augroup vimdaily
  autocmd!
  autocmd BufWinEnter,FileType * call s:Enforce()
  autocmd WinEnter * ++nested call s:CloseIfAlone()
  autocmd VimResized * call s:Resize()
  if match(join(s:lesson_lines, "\n"), '{@\d') >= 0
    autocmd TextChanged,InsertLeave * if bufnr('%') == s:main_buf | call s:Render() | endif
  endif
augroup END

if has_key(s:cfg, 'ready')
  call writefile([], s:cfg.ready)
endif

let &cpo = s:save_cpo
unlet s:save_cpo
