---
name: ytpiano2sheet
description: Transcribe a falling-bars piano video (Synthesia-style YouTube tutorial, or a local video file) into MIDI, sheet music (PDF, MusicXML, MuseScore) and audio, optionally folded onto a smaller keyboard. Use when the user shares a piano tutorial link or video with falling (or rising) notes and wants its notes, sheet music, a score, a MIDI or an audio version, or wants it to fit a 61, 49 or other smaller keyboard. Trigger phrases include "transcribe this piano video", "sheet music from this YouTube link", "make a MIDI from this tutorial", "Synthesia to MIDI", "Synthesia to sheet music", "piano tutorial to sheet music", "falling notes to MIDI", "get the notes from this piano video", "turn this video into a PDF score", "I want to play this on my 61-key keyboard". Do not use for plain audio or for a filmed piano without falling bars, since the tool reads the bars, not the sound.
argument-hint: <youtube link or video file>
---

# ytpiano2sheet

`ytpiano2sheet.py` (next to this file) reads the falling bars of a piano tutorial video. The bars are drawn from the pianist's own MIDI, so this is far more exact than transcribing the audio: on two test videos it matched a hand-verified transcription on 989 of 990 notes and an independent reading of the key presses on 947 of 947. It only works on videos that show bars falling onto (or rising from) a keyboard.

The transcriptions are for the user's own practice: the music belongs to its composers, arrangers and video creators. Do not upload, publish or share the outputs on the user's behalf; if they ask to, point them to the "Personal use and legal notes" section of the README.

Run everything with `uv run <this folder>/ytpiano2sheet.py ...`; uv installs the Python dependencies by itself. Below, `$BS` means the absolute path of this folder.

## 1. Ask first

If the user has not given a link or a video file, ask for it in plain text.

Then ask both questions in one AskUserQuestion call (skip a question the user already answered):

1. "Which outputs do you want?" (multiSelect: true)
   - "PDF sheet music" (format `pdf`)
   - "MIDI file" (format `mid`)
   - "Audio to listen to (m4a)" (format `m4a`)
   - "MusicXML + MuseScore file" (formats `musicxml,mscz`, to edit the score)
   "Other" can name `wav` or `review` (images of the bars with the detected notes).
2. "Which keyboard will you play it on?"
   - "88 keys, full piano (Recommended)" (`88`)
   - "76 keys" (`76`)
   - "61 keys" (`61`)
   - "49 keys" (`49`)
   "Other" can be another size (73, 37, 32, 25) or a range like `C2-C7` (middle C is C4). Notes outside the keyboard move by whole octaves, so the harmony stays the same.

## 2. Prerequisites

1. `uv --version`. If uv is missing, ask before installing it (`brew install uv`, or `curl -LsSf https://astral.sh/uv/install.sh | sh`).
2. `uv run $BS/ytpiano2sheet.py --check --formats <formats>`. It lists what is missing (MuseScore for pdf/mscz, fluidsynth and a soundfont for audio, ffmpeg for m4a) and the command that installs each.
3. If something is missing, ask with AskUserQuestion whether to install it, naming each item and its command (for example "brew install --cask musescore", "download a 32 MB soundfont to ~/.cache/ytpiano2sheet"). If they say yes, add `--install` to the run in step 3. If an install needs sudo (apt-get on Linux), ask the user to run that command themselves with the `!` prefix, then check again. If they say no, drop the formats that need the missing tools and say which ones you dropped.

## 3. Run

```
uv run $BS/ytpiano2sheet.py "<link or file>" -f <formats>,review -k <keyboard> -o <output folder> [--install]
```

- Always add `review`, even if the user did not ask for it: you need the images for step 4. Delete the `review` folder afterwards unless the user asked for it.
- Default output folder: an `out/` folder in the user's current project, unless they say otherwise. Each song gets its own folder inside it, named after the video title: `out/<song>/<song>.pdf` and so on, `out/<song>/review/`, and `<song> (61 keys).*` for a smaller keyboard.
- It takes about a minute for a 2-3 minute video (download included). The log reports the keyboard it found, the bar speed, the note count, and for the sheet the guessed key and meter.
- Optional flags: `--meter 3` or `--meter 4` and `--key D` / `--key d` (D minor) override the sheet's guesses. The sheet only guesses 3/4 or 4/4: when you know the piece is in a compound meter (Chopin's Nocturne Op. 9 No. 2 in 12/8, a barcarolle in 6/8), or the triplets of a piece in 4/4 (Moonlight Sonata) make the sheet far too many bars of 3/4, pass `--meter 12/8`, `6/8` or `9/8`; `--lowest C2` when the video's keyboard is cut off at an unusual key (the log warns when it had to guess); `--start` / `--end` (seconds) when an intro or end screen still turns into notes in the review images; `--threshold N` (below the logged threshold) when some bars, often dark ones, have no box.

If it stops with "could not find moving bars above the keyboard", the video has no falling bars (a filmed piano, say). Tell the user this tool cannot read it.

## 4. Check before reporting

Open a few review images with the Read tool: the first, one from the middle, the last, plus any that look dense. Every bright bar should carry a box with a note name, and no box should sit on empty space. Also check that:
- the keyboard line in the log says 88 keys A0-C8, or a sensible smaller range;
- the sheet's key matches the music (the log line `sheet: key ...`);
- for a PDF, page 1 looks like piano music. Convert it with `sips -s format png <pdf> --out page1.png` on macOS, or MuseScore `-o page1.png`, then view it.

If bars are missing or wrong, say so with timestamps rather than hiding it. Try the flags first: `--meter` or `--key` when only the sheet's meter or key looks wrong (the MIDI does not change), `--lowest` when the keyboard range in the log is off.

If the reader itself is wrong and no flag helps, do not edit the scripts silently. A fix that makes one video work can break others, and the broken result still looks like plausible sheet music. Explain what looks wrong and why, then ask with AskUserQuestion whether to patch the local copy. If the user agrees:
1. Before editing, copy each script you will change to the song's folder (`ytpiano2sheet.py.orig`, `sheet.py.orig`).
2. Make the smallest change that fixes the cause, rerun, and redo the checks above. Then run `uv run $BS/test_ytpiano2sheet.py`; if it fails, put the originals back.
3. Write `bug-report.md` in the song's folder: the video link, the timestamps that were wrong, the relevant log lines, and `diff -u` of each change against its `.orig`, so the author can test the fix against the reference videos and ship it to everyone.
4. Ask with AskUserQuestion whether to open an issue upstream with that report. Only on a yes, and only if `gh` is installed and logged in: `gh issue create --repo <owner/name from git -C $BS remote get-url origin> --title "<short description of the video style that failed>" --body-file <song folder>/bug-report.md`, then give the user the issue link. Without `gh`, or if $BS is not a git clone, tell the user where `bug-report.md` is so they can post it themselves.

## 5. Report

List the files written, the note count, the key and meter used, any notes moved by the keyboard fold (the log line `keyboard ...: N notes moved`), and anything from step 4 that looked off. If the scripts were patched, say so plainly and point to `bug-report.md`. Keep it short.
