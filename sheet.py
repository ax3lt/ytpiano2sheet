"""Piano performance MIDI (real seconds, no bar lines) -> MusicXML, plus .mscz and .pdf when MuseScore is installed.

MuseScore's own MIDI import comes out unreadable (stray voices, clef flips, rests everywhere), so the notation is built here with music21 and MuseScore only engraves it.
"""
import os, subprocess, sys
import librosa, mido, numpy as np
from music21 import chord, clef, duration, key as m21key, layout, metadata, meter as m21meter, note, pitch, stream, tempo

SR, HOP = 22050, 512
GRID = 2  # snap to 8th notes; on Je te laisserai des mots a 16th grid fit only 1% more onsets
SPLIT = 60  # ponytail: fixed hand split at middle C, a moving split would follow the melody better
SLIP = 4  # cost of a bar that breaks the meter, against one bass note landing on beat one


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def read(src):
    notes, held, t = [], {}, 0.0
    for msg in mido.MidiFile(src):
        t += msg.time
        if msg.type == 'note_on' and msg.velocity > 0:
            held[msg.note] = (t, msg.velocity)
        elif msg.type in ('note_on', 'note_off') and msg.note in held:
            s, v = held.pop(msg.note)
            notes.append((s, t, msg.note, v))
    return sorted(notes)


def guess_key(notes):
    s = stream.Stream()
    for st, e, p, v in notes:
        s.append(note.Note(p, quarterLength=max(0.25, round((e - st) * 4) / 4)))
    k = s.analyze('key')
    # Key profiles can't tell a key from its relative (D major / B minor share every note): the bass can
    bass = lambda pc: sum(e - st for st, e, p, v in notes if p < 55 and p % 12 == pc)
    if bass(k.relative.tonic.pitchClass) > 1.5 * bass(k.tonic.pitchClass):
        k = k.relative
    return k.tonic.name if k.mode == 'major' else k.tonic.name.lower()


def downbeats(beats, notes, meter):
    """Beat one is where the bass is lowest: in a waltz the chord notes on beats two and three sit in the bass register too, so "is it low" alone can't tell. Viterbi over position in bar; the beat tracker can gain or lose a beat in rubato and some arrangements really have an odd bar, so a bar may end early (2+ beats) or run one long, at a cost."""
    n = len(beats)
    pos = lambda x: float(np.interp(x, beats, np.arange(n)))
    lowest = np.full(n + meter, np.inf)
    for s, e, p, v in notes:
        b = pos(s)
        if abs(b - round(b)) < 0.25:
            lowest[round(b)] = min(lowest[round(b)], p)
    bass = [1.0 if lowest[k] < min(lowest[k + 1:k + meter]) else 0.0 for k in range(n)]
    score = np.array([bass[0] if s == 0 else 0.0 for s in range(meter)])
    back = np.zeros((n, meter), int)
    for k in range(1, n):
        new = np.full(meter, -np.inf)
        for s in range(meter):
            for q in range(meter):
                if (q + 1) % meter == s:
                    c = 0
                elif (s == 0 and q >= 1) or (s == q == meter - 1):
                    c = SLIP
                else:
                    continue
                if score[q] - c > new[s]:
                    new[s], back[k][s] = score[q] - c, q
            new[s] += bass[k] if s == 0 else 0
        score = new
    path = [int(np.argmax(score))]
    for k in range(n - 1, 0, -1):
        path.append(back[k][path[-1]])
    path = path[::-1]
    downs = [k for k in range(n) if path[k] == 0]
    odd = [(round(beats[a], 1), b - a) for a, b in zip(downs, downs[1:]) if b - a != meter]
    hit = np.mean([bass[k] for k in downs])
    return downs, odd, hit - len(odd) / len(downs)


def track_beats(notes):
    """Beat times from the note onsets themselves (on sustained piano audio the onset envelope is too smeared to lock onto), extended past both ends at the edge spacing."""
    frame = lambda x: int(round(x * SR / HOP))
    env = np.zeros(frame(notes[-1][1]) + 50)
    for s, e, p, v in notes:
        env[frame(s)] += v * (1.5 if p < 55 else 1.0)
    env = np.convolve(env, np.exp(-0.5 * np.arange(-3, 4) ** 2), 'same')
    _, beats = librosa.beat.beat_track(onset_envelope=env, sr=SR, hop_length=HOP, start_bpm=120, tightness=100, units='time')
    lo, hi = beats[1] - beats[0], beats[-1] - beats[-2]
    while beats[0] > notes[0][0] - lo:
        beats = np.insert(beats, 0, beats[0] - lo)
    while beats[-1] < max(n[1] for n in notes) + hi:
        beats = np.append(beats, beats[-1] + hi)
    return beats


def write(notes, stem, title, formats=('musicxml', 'pdf', 'mscz'), mscore=None, meter=None, key=None):
    """notes: (start s, end s, midi, velocity). Writes whichever of stem.musicxml/.pdf/.mscz the formats ask for and returns their paths. meter: beats per bar (3 or 4 quarters), or '6/8', '9/8', '12/8' (groups of three eighths); default guessed, 3 or 4."""
    notes = sorted(notes)
    key = key or guess_key(notes)
    key_sig = m21key.Key(key)
    scale = {p.pitchClass: p.name for p in key_sig.pitches}
    if key_sig.mode == 'minor':  # the raised leading tone, C# in D minor
        lead = key_sig.tonic.transpose('m-2')
        scale[lead.pitchClass] = lead.name

    fifths = lambda p: {'F': -1, 'C': 0, 'G': 1, 'D': 2, 'A': 3, 'E': 4, 'B': 5}[p.step] + 7 * int(p.alter)
    center = key_sig.sharps + 2  # middle of the key's seven notes on the line of fifths

    def spell(midi):
        # From a MIDI number music21 marks white keys as explicit naturals and may pick flats in a sharp key; spell from the scale, else as the spelling closest to the key on the line of fifths (F# not Gb in D minor, Eb not D# there)
        p = pitch.Pitch(midi=midi)
        if p.pitchClass in scale:
            p.name = scale[p.pitchClass]
        elif p.accidental is not None and abs(fifths(p.getEnharmonic()) - center) < abs(fifths(p) - center):
            p = p.getEnharmonic()
        if p.accidental is not None and p.accidental.alter == 0:
            p.accidental = None
        return p

    beats = track_beats(notes)
    num, _, den = str(meter or '').partition('/')
    unit = 0.5 if den == '8' else 1.0  # quarter lengths per beat: compound meters count eighths
    if unit == 0.5:
        # Compound: the bass can't tell where a 12/8 bar starts (a lower bass mid-bar is common), so find the groups of three eighths first, then start the bar on the group whose bass is lowest most often
        if 60 / np.median(np.diff(beats)) < 80:  # the tracker followed the dotted quarter: split it in three
            beats = np.interp(np.arange(0, len(beats) - 1 + 1e-9, 1 / 3), np.arange(len(beats)), beats)
        pos = lambda x: float(np.interp(x, beats, np.arange(len(beats))))
        groups, meter = int(num) // 3, int(num)
        dg, odd, _ = downbeats(beats, notes, 3)
        low = [min((p for s, e, p, v in notes if abs(pos(s) - k) < 0.25), default=200) for k in dg]
        phase = max(range(groups), key=lambda ph: sum(low[i] <= min(low[i:i + groups]) for i in range(ph, len(dg), groups)))
        downs = dg[phase::groups]
        if phase: downs = [downs[0] - 3 * groups] + downs
        odd = [(round(beats[a], 1), b - a) for a, b in zip(downs, downs[1:]) if b - a != meter]
        log(f'sheet: key {key_sig}, {meter}/8, {len(downs)} bars, odd bars (time s, eighths): {odd}')
    else:
        pos = lambda x: float(np.interp(x, beats, np.arange(len(beats))))
        fits = {m: downbeats(beats, notes, m) for m in ([int(num)] if num else (3, 4))}
        meter = max(fits, key=lambda m: fits[m][2])
        downs, odd, _ = fits[meter]
        log(f'sheet: key {key_sig}, {meter}/4 ' + ' '.join(f'({m}/4 fit {f[2]:.2f})' for m, f in fits.items())
            + f', {len(downs)} bars, odd bars (time s, beats): {odd}')

    # Everything in quarter notes from the first downbeat at or before the first note
    start = max([k for k in downs if k <= pos(notes[0][0]) + 0.25], default=downs[0] - meter)
    snap = lambda b: round((b - start) * GRID) / GRID * unit  # in quarter lengths
    hands = {True: {}, False: {}}  # right hand?: onset -> [(end, pitch)]
    for s, e, p, v in notes:
        a = snap(pos(s))
        b = max(snap(pos(e)), a + unit / GRID)
        hands[p >= SPLIT].setdefault(a, []).append((b, p))

    score_ = stream.Score()
    score_.metadata = metadata.Metadata(title=title, composer=' ')  # blank, or music21 signs it "Music21"
    bpm = 60 / np.median(np.diff(beats))
    total = max(b for h in hands.values() for ev in h.values() for b, _ in ev)
    bar_starts = [(k - start) * unit for k in downs if k >= start] + [None]
    if bar_starts[0] > 0:  # notes before the first tracked downbeat: a full bar of leading rests
        bar_starts.insert(0, 0)
    parts = []
    for right in (True, False):
        part = stream.PartStaff()
        part.insert(0, clef.TrebleClef() if right else clef.BassClef())
        part.insert(0, m21key.Key(key))
        if right:
            part.insert(0, tempo.MetronomeMark(number=round(bpm)) if unit == 1 else
                        tempo.MetronomeMark(number=round(bpm / 3), referent=duration.Duration(1.5)))
        last_len = None  # time signature at the first bar and wherever the bar length changes
        for a, b in zip(bar_starts, bar_starts[1:]):
            length = (b if b is not None else a + meter * unit) - a
            if length != last_len:
                part.insert(a, m21meter.TimeSignature(f'{round(length / unit)}/{4 if unit == 1 else 8}'))
                last_len = length
        onsets = sorted(hands[right])
        now = 0.0
        for i, a in enumerate(onsets):
            if a > now:
                part.insert(now, note.Rest(quarterLength=a - now))
            nxt = onsets[i + 1] if i + 1 < len(onsets) else total
            end = min(nxt, max(b for b, _ in hands[right][a]))
            if nxt - end < unit:  # a gap under a beat reads as clutter, play it legato
                end = nxt
            pitches = [spell(p) for p in sorted({p for _, p in hands[right][a]})]
            el = note.Note(pitches[0]) if len(pitches) == 1 else chord.Chord(pitches)
            el.quarterLength = end - a
            part.insert(a, el)
            now = end
        part.makeNotation(inPlace=True)
        parts.append(part)
        score_.insert(0, part)
    score_.insert(0, layout.StaffGroup(parts, symbol='brace', barTogether=True))
    score_.write('musicxml', fp=stem + '.musicxml')
    written = [stem + '.musicxml']
    for ext in ('mscz', 'pdf'):
        if ext not in formats:
            continue
        out = f'{stem}.{ext}'
        if os.path.exists(out):
            os.remove(out)
        # MuseScore 4.7 often aborts while shutting down, after the file is written
        subprocess.run([mscore, '-o', out, stem + '.musicxml'], capture_output=True)
        assert os.path.exists(out), f'MuseScore did not write {out}'
        written.append(out)
    if 'musicxml' not in formats:  # only needed to hand the score to MuseScore
        os.remove(stem + '.musicxml'); written.pop(0)
    log(f'sheet: {len(notes)} notes, {bpm:.0f} bpm')
    return written


if __name__ == '__main__':  # sheet.py <in.mid> <out-stem> [beats-per-bar] [key]: MusicXML only
    src, stem = sys.argv[1], sys.argv[2]
    write(read(src), stem, os.path.basename(stem), ('musicxml',), None, sys.argv[3] if len(sys.argv) > 3 else None, sys.argv[4] if len(sys.argv) > 4 else None)
