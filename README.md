# ytpiano2sheet: piano tutorial video to MIDI and sheet music

A [Claude Code](https://claude.com/claude-code) skill that turns a falling-notes piano tutorial video (the Synthesia style that is everywhere on YouTube) into a MIDI file, PDF sheet music, MusicXML, a MuseScore score and audio, so you can practise the piece from a score instead of pausing a video. Give Claude a link, say which outputs you want and what keyboard you play, and it transcribes the video, checks its own work, and reports. When a video's style trips up the reader, Claude explains what went wrong and, with your permission, patches the code for it.

It works as a Synthesia to MIDI converter and a YouTube piano tutorial to sheet music converter, from a link or a video file, and runs without Claude too, as a plain command-line tool. Free and open source.

> **For personal practice only.** The music in these videos belongs to its composers, arrangers and the creators of the videos. Use what this tool writes to learn a piece yourself; do not share, upload or sell it. See [Personal use and legal notes](#personal-use-and-legal-notes).

## Install the skill

You need [Claude Code](https://claude.com/claude-code) and [uv](https://docs.astral.sh/uv/) (`brew install uv`, or `curl -LsSf https://astral.sh/uv/install.sh | sh`). Then, inside Claude Code:

```
/plugin install ytpiano2sheet --marketplace ax3lt/ytpiano2sheet
```

or from your shell:

```
claude plugin marketplace add ax3lt/ytpiano2sheet
claude plugin install ytpiano2sheet@ytpiano2sheet
```

Update later with `claude plugin update ytpiano2sheet@ytpiano2sheet`. If you would rather keep the skill as a git checkout (handy when Claude patches it for you, see below), clone it into your skills folder instead:

```
git clone https://github.com/ax3lt/ytpiano2sheet ~/.claude/skills/ytpiano2sheet
```

Claude checks the rest (MuseScore for PDFs, FluidSynth and ffmpeg for audio) the first time and asks before installing anything.

## Use it

Ask Claude in your own words, or call the skill directly:

```
> make sheet music from https://youtu.be/ctCa6JlA1R4, I have a 61-key keyboard
> /ytpiano2sheet:ytpiano2sheet https://youtu.be/ddbcuxjtGaA
```
Claude then:

1. asks which outputs you want (PDF, MIDI, audio, MusicXML and MuseScore) and which keyboard you will play on;
2. checks the prerequisites for those outputs and asks before installing any;
3. runs the transcription, about a minute for a 3-minute video;
4. looks at the review images (the bars with every detected note boxed) and at the score's key and meter, and fixes what a flag can fix (meter, key, lowest key, intro or end screen to skip, threshold for dark bars);
5. reports the files written to `out/<video title>/` and anything that still looked off.

## When a video does not read right

Every channel draws its bars differently, and some styles still trip the reader (see [Accuracy and limits](#accuracy-and-limits)). When no flag helps, Claude does not edit the code silently. It shows you what is wrong, with timestamps, and asks whether to patch your copy. If you agree, it:

1. backs up the scripts it will change into the song's folder (`ytpiano2sheet.py.orig`);
2. makes the smallest change that fixes the cause, reruns the video and the self-test, and puts the originals back if the test fails;
3. writes `bug-report.md` in the song's folder: the link, the wrong timestamps, the log lines and the diff;
4. offers to open an issue here with that report (it asks first, and needs the [GitHub CLI](https://cli.github.com)).

A fix that makes one video work can break others, so local patches stay local. Reported fixes get tested against the reference videos before they ship to everyone. With the plugin install, `claude plugin update` replaces your copy, patch included, with the latest release. With the git checkout, `git -C ~/.claude/skills/ytpiano2sheet status` shows what Claude changed, `git checkout .` drops it once the fix is upstream, and `git pull` updates.

## Why read the bars

The bars in these videos are drawn from the pianist's own MIDI recording. Reading them gives every note's key, start and length exactly, with no guessing from sound. Audio transcription of the same video invented 147 notes and missed 24; reading the bars matched a hand-verified transcription on 989 of 990 notes.

## FAQ

### How do I convert a Synthesia video to MIDI?

Ask Claude "make a MIDI from <YouTube link>" with the skill installed, or run `uv run ytpiano2sheet.py <YouTube link> -f mid`. The MIDI has every note with its exact timing, read from the falling bars.

### How do I get sheet music from a YouTube piano tutorial?

Ask Claude for "sheet music from <YouTube link>", or run `uv run ytpiano2sheet.py <YouTube link> -f pdf`. You get a two-staff PDF score (MuseScore engraves it). Add `musicxml` or `mscz` to edit the score in MuseScore, Sibelius, Finale or Dorico.

### Can it fit the song on a 61-key or 49-key keyboard?

Yes: `-k 61`, `-k 49` or any range like `-k C2-C7`. Notes outside your keyboard move by whole octaves, so the harmony stays the same.

### Which videos does it work with?

Falling-notes (or rising-notes) piano videos with a keyboard at the bottom: Synthesia, Embers, MIDI visualizers and the custom styles of most tutorial channels, from 480p to 1080p, with 19 to 88 keys. See [Accuracy and limits](#accuracy-and-limits).

### Can it transcribe piano audio or a video of a real piano?

No. It reads the bars, not the sound. For a recording without bars you need an audio-to-MIDI transcription tool instead.

## How it works

1. Downloads the video (yt-dlp) or opens a local file.
2. Takes the median of 60 frames spread over the video: everything that moves (bars, hands, particles) disappears and the bare keyboard is left. Black keys are found high on the keys, the white-key grid from the lines between white keys, and the note names from the groups of two and three black keys.
3. Measures how fast the bars fall (or rise) by timing the same pattern from one row of pixels to the next.
4. Builds a piano roll: every row above the keyboard sees each bar at a different moment; shifted back by the travel time, all rows agree on the bars and disagree on everything else (sparks, particles, glow, logos), so their median keeps just the bars. Frames where much of the screen lights up at once (title cards, fades, end screens) are left out, and pop-ups wider than any chord of bars are cut from the roll.
5. Each bright run in the roll goes to the key whose centre is nearest; runs too wide for one bar are split over the keys whose bars fit them best.
6. Writes the formats you asked for. The sheet music is built with music21 (beats from the note onsets, bar lines from where the bass is lowest, hands split at middle C) and engraved by MuseScore.

Nothing is tuned to a particular video: keyboard position and size, bar speed and direction, brightness threshold and bar widths are all measured.

## Requirements

- [uv](https://docs.astral.sh/uv/). It fetches Python 3.11 to 3.13 if needed and installs the packages (numpy, OpenCV, mido, yt-dlp, music21, librosa) on first run.
- For `pdf` and `mscz`: [MuseScore](https://musescore.org/download) 3 or 4 (`brew install --cask musescore`).
- For `wav` and `m4a`: [FluidSynth](https://www.fluidsynth.org) (`brew install fluid-synth`) and a General MIDI soundfont, downloaded on request ([GeneralUser GS](https://github.com/mrbumpy409/GeneralUser-GS), 32 MB, to `~/.cache/ytpiano2sheet`).
- For `m4a`: [ffmpeg](https://ffmpeg.org) (`brew install ffmpeg`).

Only what your chosen formats need is checked. `--check` lists what is missing and how to install it; `--install` installs it (Homebrew on macOS, apt-get on Debian/Ubuntu, otherwise it tells you where to get it). Developed and tested on macOS; Linux should work, Windows is untested.

## Command line (without Claude)

The same script runs on its own. From the cloned folder:

```
uv run ytpiano2sheet.py <link or video file> [options]
```

Run in a terminal without options and it asks which outputs you want, what keyboard you have, and whether to install anything missing. In a script it uses the defaults instead.

| option | meaning |
|---|---|
| `-f`, `--formats` | comma separated: `mid`, `musicxml`, `pdf`, `mscz`, `wav`, `m4a`, `review` (default `mid,musicxml,pdf`) |
| `-k`, `--keys` | fold the notes onto your keyboard: `88`, `76`, `73`, `61`, `49`, `37`, `32`, `25`, or a range like `C2-C7` (middle C is C4). Default 88 |
| `-o`, `--out` | output folder (default `out`); each song gets a subfolder named after the video title |
| `--install` | install missing prerequisites without asking |
| `--check` | only check prerequisites for `--formats` |
| `--meter` | meter for the sheet: `3` or `4` (quarters per bar), or `6/8`, `9/8`, `12/8` (default: guessed between 3/4 and 4/4) |
| `--key` | key for the sheet, e.g. `D`, or `d` for D minor (default: guessed) |
| `--lowest` | lowest key shown in the video (e.g. `C2`) when its keyboard is cut off at an unusual key |
| `--start`, `--end` | only read the video between these times in seconds (title cards and end screens are usually skipped on their own) |
| `--threshold` | brightness a bar must reach, when some bars are drawn darker than the measured threshold and get missed |

Examples:

```
uv run ytpiano2sheet.py https://youtu.be/ddbcuxjtGaA -f pdf,m4a
uv run ytpiano2sheet.py https://youtu.be/ctCa6JlA1R4 -f mid,pdf -k 61 -o ~/Music/sheets
uv run ytpiano2sheet.py my-recording.mp4 -f mid --check
uv run ytpiano2sheet.py https://youtu.be/p29JUpsOSTE -f pdf --meter 12/8
```

## Outputs

Files are named after the video title, in `out/<title>/`:

- `.mid`: the notes as played (key-press lengths, velocity 80, no pedal).
- `.musicxml`, `.mscz`, `.pdf`: two-staff piano score with key signature, meter, tempo and bar lines.
- `.wav`, `.m4a`: the notes rendered with the soundfont. The bars show key presses, not the sustain pedal, so for listening each note rings until the next note in the same hand (at most 2 s).
- `review/`: images of the bar roll with every detected note boxed and labelled, 8 seconds each, for checking by eye.

With a smaller keyboard (`-k 61`) the files are named `<title> (61 keys).*`.

## Smaller keyboards

With `-k 61` (or any size or range), notes outside your keyboard move up or down by whole octaves until they fit. The note names stay, so the harmony does not change; a note that lands on a key already held merges into it. The log says how many notes moved.

## Tests

```
uv run test_ytpiano2sheet.py
```

Draws a synthetic falling-bars video with known notes (stepwise melody, chords, neighbouring bars pressed together, repeated, very short and 2-frame notes, both ends of the keyboard, bar lines, a flash, a static logo and particles), reads it back and checks every note, plus the pop-up filter and the keyboard fold. It prints `SELFTEST OK`.

## Accuracy and limits

Tested on two hand-checked videos: "Je te laisserai des mots" (30 fps, gradient bars, moon logo, sparks; 989 of 990 hand-verified notes) and "La Maritza" (60 fps, glowing pill bars, heavy particle clouds; 947 of 947 against an independent reading of the key presses). Then on 39 more YouTube videos from about 30 channels: classic Synthesia (including 480p and 720p uploads), Rousseau, Sheet Music Boss, PHianonize, TutorialsByHugo (sheet music above the bars), keyboard strips over a camera shot, beginner channels with 19 to 37 keys, SeeMusic and Embers visualizers, vertical Shorts. Every one that has bars came out at the right pitch against its own soundtrack, and the six with a public-domain score (Für Elise, Gymnopédie No. 1, Bach's Prelude in C, Chopin's Nocturne Op. 9 No. 2) agree with it on 86 to 99% of the notes, at the right octave; the rest is the player's own version.

- It needs bars. Videos of a filmed piano, or with sheet music only, cannot be read.
- Bars drawn as hollow outlines with a strong glow (as in Patrik Pietschmann's videos) come out with neighbouring-key errors on held bass notes. Animated backgrounds (moving clouds, video behind the bars) cost notes too.
- Pedal and dynamics are not in the bars, so the MIDI has fixed velocity and no pedal.
- A very short note played on a key right next to a longer held one, entirely inside it, is taken for that bar's glow and dropped (think trills on a held note).
- The sheet's beat and bar lines are guessed from the notes; rubato can produce an odd bar now and then (the log lists them). `--meter` and `--key` fix wrong guesses. Compound meters (6/8, 9/8, 12/8) are never guessed: pass `--meter`. Hands are split at middle C.
- The lowest key is assumed for standard keyboard sizes (88, 76, 61, 49, 37 keys), and for a piano cut off by the frame edges when only one octave position fits inside A0-C8. Otherwise it guesses from middle C and warns; pass `--lowest` if needed.

## Personal use and legal notes

This is not legal advice. Copyright and download rules differ between countries, and you are responsible for how you use this tool.

- **What you transcribe is not yours.** A transcription copies the music in the video: the composition (unless it is in the public domain), the arrangement, and the performance the bars were drawn from. Even a Chopin nocturne in a tutorial is usually someone's own arrangement. Keep what this tool writes for your own practice and study. Do not publish, share, upload or sell it, or pass it off as your own arrangement, without permission from the rights holders.
- **Support the creators.** Many tutorial channels sell their sheet music or MIDI files, or offer them to supporters. If the one you are learning from does, buy it: it is usually better than any automatic transcription, and it keeps the tutorials coming.
- **Downloading.** YouTube's Terms of Service do not allow downloading videos unless YouTube offers a download option or you have permission from YouTube and the rights holders. Other sites have their own terms. Only download what you are allowed to, or run the tool on a video file you already have the right to use.
- **Trademarks.** YouTube, Synthesia and MuseScore are trademarks of their respective owners. This project is not affiliated with or endorsed by any of them; "Synthesia-style" only describes the look of the videos.
- **No warranty.** The software is provided as is, without warranty of any kind (see [LICENSE](LICENSE)). Transcriptions can contain wrong or missing notes.
- **Third-party software.** The Python packages, MuseScore, FluidSynth, ffmpeg and the GeneralUser GS soundfont are separate projects under their own licenses. None of them is included in this repository; they are installed or downloaded on your machine when you ask for them.

## License

The code is under the [MIT License](LICENSE). The license covers this code only. It gives you no rights to the music you transcribe with it.

Keywords: Synthesia to MIDI, Synthesia to sheet music, YouTube piano tutorial to MIDI, YouTube to sheet music, piano video to MIDI converter, falling notes to MIDI, piano roll video to MIDI, MIDI from video, video to MusicXML, piano tutorial to PDF score, piano transcription, learn piano from YouTube, Claude Code skill, Claude skill, agent skill.
