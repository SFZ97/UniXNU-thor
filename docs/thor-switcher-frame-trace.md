# Thor task switcher frame trace

Add `thor_switcher_trace=1` to the development boot arguments and start the
matching 22H374 kernel and UI adapter. On SpringBoard startup,
`PONG SWITCHER STATUS` reports whether the adapter found the expected native
drag and swipe-to-remove method signatures. Open the switcher and swipe cards
up to remove them. A new boot is needed for another capture.

The first drag or removal cue opens a bounded window. Cue events extend the
window by up to 950 ms at a time, with an 8 second maximum. The framebuffer
stores up to 1,152 records and the trace stores up to 96 cues. A low-priority
thread prints the records to the console after the window closes. Collect the
console persist promptly if the log rotates during a long session.

`PONG SWITCHER CUE` records phases 1 and 2 for a drag start and removal event.
`PONG SWITCHER FRAME` records BackBoard submissions to Thor's 1440×3200
framebuffer. Stages are expressed in monotonic microseconds. A frame refused
for surface readiness has zero ready, start and done timestamps. Completed
samples time CPU composition and copy. `writer_wait_us` totals sleeps waiting
for GPU writers to release the surfaces. A frame completed by native GPU
scanout is outside this trace. CPU copy completion is earlier than panel scanout.

The frame trace derives from the local Thor v156 build. That device capture
recovered 383 frame records and confirmed the runtime hook was armed. This
checkout ports the instrumentation to the public display queue and leaves its
frame dispatch, waiting, copy, and retirement policies in place. The original
device measurements and a build of this port are separate evidence.
