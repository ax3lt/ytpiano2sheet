#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = ["numpy", "opencv-python-headless", "mido", "yt-dlp", "music21", "librosa"]
# ///
"""Falling-bars piano video (YouTube link or file) -> MIDI, sheet music (MusicXML, PDF, MuseScore) and audio.

The bars in these videos are drawn from the pianist's own MIDI, so reading them gives the notes far more exactly than transcribing the audio. Everything that differs between videos (keyboard position and size, bar speed and direction, bar widths, overlays) is measured from the video itself.
"""
import argparse, bisect, itertools, os, re, shutil, subprocess, sys
import cv2, mido, numpy as np

NAMES = 'C C# D D# E F F# G G# A A# B'.split()
BLACK = {1, 3, 6, 8, 10}
name = lambda p: NAMES[p % 12] + str(p // 12 - 1)
# Lowest white key for common keyboard sizes, by number of white keys shown
LOWEST = {52: 21, 45: 28, 36: 36, 29: 36, 22: 48}
# Common keyboard sizes, lowest and highest key (scientific pitch: middle C is C4)
KEYBOARDS = {'88': ('A0', 'C8'), '76': ('E1', 'G7'), '73': ('E1', 'E7'), '61': ('C2', 'C7'),
             '49': ('C2', 'C6'), '37': ('C3', 'C6'), '32': ('F3', 'C6'), '25': ('C3', 'C5')}
FORMATS = ('mid', 'musicxml', 'pdf', 'mscz', 'wav', 'm4a', 'review')
CACHE = os.environ.get('YTPIANO2SHEET_CACHE') or os.path.join(os.path.expanduser('~'), '.cache', 'ytpiano2sheet')
SF2_URL = 'https://github.com/mrbumpy409/GeneralUser-GS/raw/main/GeneralUser-GS.sf2'
MSCORE = [os.environ.get('MSCORE', ''), '/Applications/MuseScore 4.app/Contents/MacOS/mscore',
          '/Applications/MuseScore 3.app/Contents/MacOS/mscore', 'C:/Program Files/MuseScore 4/bin/MuseScore4.exe',
          'mscore', 'mscore4portable', 'mscore3', 'musescore', 'MuseScore4']
HINTS = {'MuseScore': 'https://musescore.org/download', 'fluidsynth': 'https://www.fluidsynth.org',
         'ffmpeg': 'https://ffmpeg.org/download.html', 'soundfont': SF2_URL}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def fetch(src, out):
    if os.path.exists(src):
        return src, os.path.splitext(os.path.basename(src))[0]
    import yt_dlp
    opts = {'format': 'bv*[height<=1080][vcodec^=avc1]/bv*[height<=1080][ext=mp4]/bv*[height<=1080]/b',
            'outtmpl': os.path.join(out, '%(title)s.%(ext)s'), 'quiet': True, 'no_warnings': True, 'noprogress': True, 'restrictfilenames': False}
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(src)
        return y.prepare_filename(info), info['title']


def value(img):
    return img.max(-1)  # brightness as HSV value: a pure red bar is as bright as a white one


def sample_frames(cap, n=60):
    """n frames spread over the video (value channel). Their median is the video with everything that moves (bars, hands, presses, particles) removed."""
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)); frames = []
    for i in range(n):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * (i + 0.5) / n)); ok, f = cap.read()
        if ok: frames.append(value(f))
    return np.array(frames)


def runs(mask):
    d = np.diff(np.r_[0, mask.astype(np.int8), 0])
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def keyboard(still, lowest=None):
    """Keys from the still (median) frame: list of (midi, center x, is_black), and the keyboard top row."""
    h, w = still.shape
    m = still.mean(1); th = max(15, 0.25 * np.percentile(m, 95))  # not m.max(): a glow line on the keys outshines them
    b = h - 1
    while b > 0 and m[b] < th: b -= 1                      # lowest bright row
    t = b
    while t > 0 and m[t - 1] >= th: t -= 1                  # up to the top of the bright band
    def black_keys(row):
        """Dark runs of a row profile that could be black keys: the dark cut-off, their centres, how many are credible."""
        # Dark or lit against the brightest key nearby, not the whole row: a glow spot, a vignette or lighting that follows the hands can leave the ends of the keyboard far dimmer than the middle. The floor keeps the background beside a short keyboard dark.
        r = w // 24; near = np.lib.stride_tricks.sliding_window_view(np.pad(row, r, mode='edge'), 2 * r + 1).max(1)
        half = np.maximum(near / 2, 0.15 * np.percentile(row, 90))
        dr = [(s, e) for s, e in runs(row < half) if s > 0 and e < w]
        typ = np.median([e - s for s, e in dr]) if dr else 0
        xb = np.array([(s + e) / 2 for s, e in dr if 0.5 * typ < e - s < 2 * typ])
        if len(xb) < 5: return half, xb, 0
        # Credible: as wide as black keys (0.5-0.65 of a white key; gaps between white keys and printed note names are thinner), in groups of two and three (about 2 gaps in 5 are wide), and with the fifth neighbour an octave away, as on any keyboard (too few keys to tell: count them all)
        g = np.diff(xb); d = xb[5:] - xb[:-5] if len(xb) >= 10 else None
        ww = (np.median(d) if d is not None else 5 * g.mean()) / 7
        if typ < 0.4 * ww or not 0.25 < (g > 1.25 * np.percentile(g, 20)).mean() < 0.55: return half, xb, 0
        return half, xb, int((np.abs(d / np.median(d) - 1) < 0.06).sum()) if d is not None else len(xb)

    # Black keys: dark runs on a row high on the keys, where hands and smoke rarely reach
    row = np.median(still[t + (b - t) * 20 // 100:t + (b - t) * 30 // 100].astype(int), 0)
    half, xb, score = black_keys(row); seps = None
    rowat = lambda y: np.median(still[max(0, y - h // 160):y + h // 160 + 1].astype(int), 0)  # taller than printed text
    best = max(range(h // 5, h - 1, 2), key=lambda y: (black_keys(rowat(y))[2], -y)) if not score else None
    if best is not None and black_keys(rowat(best))[2]:
        # The lowest bright band is not the keyboard (credits under it, a grey background or a camera shot of a real piano merged with it): take the row whose black keys are most credible, the topmost if several are (bars land on a keyboard strip, not on the filmed piano under it), and the keys around it
        row = rowat(best); half, xb, _ = black_keys(row); white = row >= half; ref = row[white].mean()
        t = b = best
        while t > 0 and still[t - 1, white].mean() > 0.5 * ref: t -= 1
        while b < h - 1 and still[b + 1, white].mean() > 0.5 * ref: b += 1
        seps = xb  # what is below the black keys may belong to something else: phase from the black keys
    gaps = np.diff(xb)
    octave = np.median(xb[5:] - xb[:-5])                      # 5 black keys to the octave, 7 white keys
    ww = octave / 7
    # White keys are a uniform grid. Its phase comes from the thin lines between whites, low on the keys
    low = np.median(still[t + (b - t) * 75 // 100:t + (b - t) * 92 // 100].astype(int), 0)
    seps = seps if seps is not None else [(s + e) / 2 for s, e in runs(low < (low.min() + low.max()) / 2) if e - s < 0.3 * ww]
    if len(seps) < 7:  # no visible lines: assume black keys straddle the boundaries
        seps = xb
    phase = np.angle(np.exp(2j * np.pi * np.array(seps) / ww).mean()) / (2 * np.pi) * ww
    # Group of two black keys (C#, D#): wide gap (3 semitones) before and after, narrow (2) between. C# sits on or near the C|D boundary, on real pianos and on evenly spaced visualizers alike
    wide = np.r_[True, gaps > 2.5 / 12 * octave, True]
    i2 = next(i for i in range(len(xb) - 1) if wide[i] and not wide[i + 1] and wide[i + 2])
    k_cd = round((xb[i2] - phase) / ww)                       # boundary index of C|D, so the C cell is k_cd - 1
    steps = [0, 2, 4, 5, 7, 9, 11]
    pc_of = lambda k: steps[(k - (k_cd - 1)) % 7] + 12 * ((k - (k_cd - 1)) // 7)  # semitones above that C
    # Cells inside the frame and lit on the black-key row (outside the keyboard is dark)
    lit = row > half
    cells = [k for k in range(int(np.floor(-phase / ww)) - 1, int(np.ceil((w - phase) / ww)) + 1)
             if phase + k * ww >= -0.3 * ww and phase + (k + 1) * ww <= w + 0.3 * ww
             and lit[int(np.clip(phase + (k + 0.15) * ww, 0, w - 1)):int(np.clip(phase + (k + 0.85) * ww, 1, w))].any()]
    cells = list(range(min(cells), max(cells) + 1))
    if lowest is None:
        span = pc_of(cells[-1]) - pc_of(cells[0])
        fits = [p for p in range(21, 109 - span) if p % 12 == pc_of(cells[0]) % 12]  # placements inside A0-C8
        if len(cells) in LOWEST and pc_of(cells[0]) % 12 == LOWEST[len(cells)] % 12: lowest = LOWEST[len(cells)]
        elif len(fits) == 1: lowest = fits[0]  # a piano cut by the frame edges: only one octave fits
        else:  # ponytail: assume the C nearest the middle is middle C; --lowest when the keyboard is cut oddly
            mid = (cells[0] + cells[-1]) / 2; k_c = k_cd - 1 + 7 * round((mid - (k_cd - 1)) / 7)
            lowest = 60 + pc_of(cells[0]) - pc_of(k_c)
            log(f'warning: {len(cells)} white keys is not a standard keyboard, guessed lowest key {name(lowest)}; set --lowest if wrong')
    base = lowest - pc_of(cells[0])
    assert base % 12 == 0, f'--lowest {name(lowest)} does not fit the keyboard pattern (lowest key is a {NAMES[pc_of(cells[0]) % 12]})'
    keys = []
    for k in cells:
        p = base + pc_of(k); keys.append((p, phase + (k + 0.5) * ww, False))
        if (p + 1) % 12 in BLACK and k + 1 in cells:            # black key on the boundary after this white
            near = xb[np.abs(xb - (phase + (k + 1) * ww)) < 0.4 * ww]
            keys.append((p + 1, near[0] if len(near) else phase + (k + 1) * ww, True))
    return keys, t


def bar_rows(still, top):
    """Rows the bars pass through: below the very top, or below sheet music shown above the bars (a bright band across the frame), and clear of the hit flares just above the keys."""
    h = len(still); m = still.mean(1); lo, y = int(0.04 * h), top - int(0.06 * h)
    while y > lo and m[y] < 160: y -= 1
    if lo < y < top - int(0.2 * h): lo = y + int(0.02 * h)
    return np.arange(lo, top - int(0.06 * h), max(2, h // 135))


def lit_up(sig):
    """A title card, flash or end screen (fading ones too) lights much of the bar area at once. Bars never do: in music frames of 7 test videos at most 24% of it rose 25 above the still frame, cards and fades 35-100%."""
    return (sig > 25).mean() > 0.35


def bar_speed(cap, fps, ys, bg, seconds=20):
    """Bar speed in px/frame along y (negative when bars rise from the keys), from how long the same pattern takes to get from one row to the next."""
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)); n = min(total, int(seconds * fps))
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, total // 2 - n // 2))
    pick = np.linspace(0, len(ys) - 1, 8).astype(int); sig = []
    for _ in range(n):
        ok, f = cap.read()
        if not ok: break
        sig.append(np.clip(value(f[ys[pick]]).astype(int) - bg[pick], 0, None))
    sig = np.array(sig, np.float32)                            # frames x rows x width
    sig[[lit_up(s) for s in sig]] = 0                          # a card or flash changes every row at once: no motion, a false lag 0
    sig -= sig.mean(0)
    F = np.fft.rfft(sig, 2 * len(sig), axis=0)
    dy, lag = [], []; m = len(sig) // 3                         # not c[-len(sig) // 3:]: that floors to one lag more than m
    for k in range(1, len(pick)):                              # each sampled row against the next: short lags, any speed
        c = np.fft.irfft((np.conj(F[:, k - 1]) * F[:, k]).sum(1))  # correlation by lag, summed over all columns
        c = np.r_[c[-m:], c[:m]]; L = int(np.argmax(c))
        if 0 < L < len(c) - 1: L += (c[L - 1] - c[L + 1]) / (2 * (c[L - 1] - 2 * c[L] + c[L + 1]))
        dy.append(ys[pick[k]] - ys[pick[k - 1]]); lag.append(L - m)
    dy, lag = np.array(dy, float), np.array(lag, float)
    # A pair of rows over moving scenery, or a pattern that repeats, can lock onto a wrong lag: keep the pairs that agree with the median speed when at least 5 of the 7 do
    nz = lag != 0; v0 = np.median(dy[nz] / lag[nz]) if nz.any() else 0
    good = np.abs(dy / v0 - lag) < 3 if v0 else nz
    if good.sum() >= 5: dy, lag = dy[good], lag[good]
    v = (dy * lag).sum() / (lag * lag).sum()
    spread = np.abs(dy / v - lag).max()
    log(f'bar speed {v:+.3f} px/frame ({"falling" if v > 0 else "rising"}), worst of {len(dy)}/{len(pick) - 1} row pairs disagrees by {spread:.1f} frames')
    assert abs(v) > 0.2 and spread < 3, 'could not find moving bars above the keyboard'
    return v


def read_roll(cap, ys, bg, v, top):
    """Piano roll of the bars at the keyboard line: roll[T] is what reaches the keys at frame T + t0. Every row sees each bar at a different frame; shifted back by the travel time, the rows agree on bars and disagree on everything else (particles, sparks, flares), so their median keeps the bars."""
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)); cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    shift = np.round((top - ys) / v).astype(int)               # frames from passing row y to reaching the keys
    s0, s1 = shift.min(), shift.max(); B = s1 - s0 + 1; n = len(ys)
    buf = np.zeros((B, n, bg.shape[1]), np.uint8); got = np.zeros((B, n), bool); seen = np.zeros((B, n), bool)
    roll = np.zeros((total + B, bg.shape[1]), np.uint8); rows_i = np.arange(n)

    def emit(T):
        k = (T - s0) % B
        # Unknown when most of its rows saw a card: the few left all saw the same moment (say a title before it filled the screen)
        if got[k].sum() > seen[k].sum() / 2: roll[T - s0] = np.median(buf[k][got[k]], 0)
        got[k] = seen[k] = False

    f = 0
    while True:
        ok, frame = cap.read()
        if not ok: break
        k = (f + shift - s0) % B
        sig = np.clip(value(frame[ys]).astype(int) - bg, 0, 255)
        seen[k, rows_i] = True
        if not lit_up(sig): buf[k, rows_i] = sig; got[k, rows_i] = True  # card frames are left to the other rows
        emit(f + s0)                                           # the last row for this keyboard frame just arrived
        f += 1
        if f % 600 == 0: log(f'  read {f}/{total} frames')
    for T in range(f + s0, f + s1): emit(T)
    return roll[:f + B - 1], s0


def otsu(vals):
    hist = np.bincount(vals, minlength=256).astype(float); p = hist / hist.sum(); best, th = -1, 128
    for t in range(1, 255):
        w0, w1 = p[:t].sum(), p[t:].sum()
        if w0 == 0 or w1 == 0: continue
        m0 = (np.arange(t) * p[:t]).sum() / w0; m1 = (np.arange(t, 256) * p[t:]).sum() / w1
        if w0 * w1 * (m0 - m1) ** 2 > best: best, th = w0 * w1 * (m0 - m1) ** 2, t
    return th


def bar_threshold(v):
    """Brightness a bar must reach, from the bright roll pixels v: Otsu's split between background and bars. When one hand's bars are drawn much darker they form their own peak under that split (Canon in D: 123 against a split at 134), and the valley below that peak is used instead."""
    th = max(otsu(v), 40)
    h = np.convolve(np.bincount(v, minlength=256).astype(float), np.ones(9) / 9, 'same')
    lo = th // 2; p = lo + int(np.argmax(h[lo:th]))
    below = h[lo:p].min() if p > lo + 4 else h[p]; above = h[p:th + 1].min()
    if h[p] > 4 * max(below, above, 1) and h[p - 8:p + 9].sum() > 0.02 * h[16:].sum(): return lo + int(np.argmin(h[lo:p]))
    return th


def junk_boxes(bright, ww, fps):
    """Boxes (first row, end row, x0, x1) around pop-ups, flashes and end screens in the roll: a run lit across more than 6 white keys in one row, which separate bars never make. Long ones get a margin for fading edges."""
    wide = [[(s, e) for s, e in runs(r) if e - s > 6 * ww] for r in bright]
    boxes = []
    for a, b in runs(np.array([bool(w) for w in wide])):
        x0 = min(s for T in range(a, b) for s, _ in wide[T]); x1 = max(e for T in range(a, b) for _, e in wide[T])
        while a > 0 and bright[a - 1, x0:x1].mean() > 0.5: a -= 1         # a ragged or fading edge: text and streaks
        while b < len(bright) and bright[b, x0:x1].mean() > 0.5: b += 1    # break the wide run, the area stays lit
        m = int(0.5 * fps) if b - a > 3 else 0
        boxes.append((max(0, a - m), min(len(bright), b + m), max(0, int(x0 - ww)), int(x1 + ww)))
    return boxes


def fit_keys(roll, keys):
    """Keys with their centres moved onto the bars, for videos that draw the bars to a slightly different scale than the keyboard picture under them (La Campanella by Kassia: 1.8% narrower, so bars high and low on the keyboard land between two keys). The scale is the bars' own octave, where their positions repeat; then a shift within a quarter white key. Nothing changes below a quarter key of drift at the keyboard ends: in 19 other test videos the bars' octave matched the keys' to 0.7%."""
    centers = np.array([x for _, x, _ in keys]); white = ~np.array([b for _, _, b in keys]); ww = np.median(np.diff(centers[white]))
    c = [(s + e) / 2 for r in roll > max(otsu(roll[roll > 15]), 40) for s, e in runs(r) if 0.4 * ww < e - s < 1.3 * ww]  # one bar wide
    h = np.convolve(np.bincount(np.round(c).astype(int), minlength=roll.shape[1] + 1).astype(float), np.ones(3) / 3, 'same')
    L = np.arange(int(0.95 * 7 * ww), int(1.05 * 7 * ww) + 1)
    ac = np.array([(h[:-l] * h[l:]).sum() for l in L]); k = int(np.argmax(ac))
    if not 0 < k < len(L) - 1: return keys
    d = ac[k - 1] - 2 * ac[k] + ac[k + 1]
    a = (L[k] + ((ac[k - 1] - ac[k + 1]) / (2 * d) if d else 0)) / (7 * ww)
    if abs(a - 1) * white.sum() / 2 < 0.25: return keys
    mid = centers.mean(); g = np.sqrt(h)
    b = max(np.arange(-0.25 * ww, 0.25 * ww, 0.5), key=lambda b: g[np.clip(np.round(mid + a * (centers - mid) + b).astype(int), 0, len(g) - 1)].sum())
    log(f'key centres fitted to the bars: scale {a:.3f}, shift {b:+.1f} px')
    return [(p, mid + a * (x - mid) + b, bl) for p, x, bl in keys]


def bars_to_notes(roll, keys, fps, th=None):
    """Notes (start frame, end frame, midi) from the roll. Each row's bright runs go to the key whose centre is nearest; a run too wide for one bar is split over the set of keys whose bars fit it best."""
    centers = np.array([x for _, x, _ in keys]); black = np.array([b for _, _, b in keys])
    ww = np.median(np.diff(centers[~black]))
    junk = junk_boxes(roll > max(otsu(roll[roll > 15]), 40), ww, fps)
    if not th:  # measured without the junk, which can be far brighter than any bar
        ok = np.ones(roll.shape, bool)
        for a, b, x0, x1 in junk: ok[a:b, x0:x1] = False
        v = roll[ok]; th = bar_threshold(v[v > 15]); del ok, v
    # Hysteresis: a bar is a bright core plus whatever dimmer pixels connect to it (some bars start dim, or dim on one side, where the background was bright)
    n, lab = cv2.connectedComponents((roll > 0.55 * th).astype(np.uint8), connectivity=4)
    keep = np.zeros(n, bool); keep[np.unique(lab[roll > th])] = True; keep[0] = False
    mask = keep[lab]; del lab
    for a, b, x0, x1 in junk:  # inside a box only what is held straight through it survives
        mask[a:b, x0:x1] = (mask[a - 1, x0:x1] if a > 0 else False) & (mask[b, x0:x1] if b < len(mask) else False)
    width = {False: 1.1 * ww, True: 0.65 * ww}                 # first guess of bar widths, measured below
    def joined(rr):
        # a letter printed on a bar splits it into pieces; pieces that together fit one bar are one bar
        out = []
        for s, e in rr:
            if out and e - out[-1][0] <= 1.1 * ww: out[-1] = (out[-1][0], e)
            else: out.append((s, e))
        return out
    rows = [[(s, e) for s, e in joined(runs(r)) if e - s > 0.3 * ww] for r in mask]
    singles = {False: [], True: []}
    for rr in rows:
        for s, e in rr:
            if e - s < 1.45 * ww:
                singles[bool(black[np.argmin(np.abs(centers - (s + e) / 2))])].append(e - s)
    for b in (False, True):
        if len(singles[b]) > 20: width[b] = np.percentile(singles[b], 75)  # upper quartile: bar ends taper

    def keys_for(s, e):
        if e - s < min(1.45 * ww, 1.15 * width[False]): return [int(np.argmin(np.abs(centers - (s + e) / 2)))]
        near = [i for i in range(len(keys)) if s - 0.3 * ww < centers[i] < e + 0.3 * ww]
        best = min((c for r in (1, 2, 3, 4) for c in itertools.combinations(near, r)),
                   key=lambda c: abs(s - min(centers[i] - width[black[i]] / 2 for i in c))
                   + abs(e - max(centers[i] + width[black[i]] / 2 for i in c)) + 0.2 * ww * len(c))
        return list(best)

    on = np.zeros((len(roll), len(keys)), bool); owner = {}
    for T, rr in enumerate(rows):
        for s, e in rr:
            ks = keys_for(s, e)
            on[T, ks] = True
            for i in ks: owner[T, i] = (s, e, tuple(ks))
    notes = []
    for i in range(len(keys)):
        for s, e in runs(on[:, i]):
            if e - s < 3:
                # a short piece where two bars touch end to start belongs to the neighbour, not to this key, unless every row of it is one whole bar of this key (fast notes last 2 frames at 25 fps)
                x0, x1, ks = owner[s, i]
                touch = [T for T in (s - 1, e) if 0 <= T < len(rows) and any(
                    a < x1 and x0 < b and keys_for(a, b) != list(ks) for a, b in rows[T])]
                whole = all(len(owner[T, i][2]) == 1 and abs(sum(owner[T, i][:2]) / 2 - centers[i]) < 0.25 * ww
                            and 0.7 < (owner[T, i][1] - owner[T, i][0]) / width[black[i]] < 1.3 for T in range(s, e))
                # one frame: only a clean full-brightness bar, and only while a frame is long enough to be a note (staccato at 30 fps; a 60 fps frame is a glitch)
                if (touch and not whole) or (e - s < 2 and (touch or not whole or fps > 32 or roll[s, x0:x1].max() < 1.3 * th)): continue
            single = [owner[T, i][1] - owner[T, i][0] for T in range(s, e) if len(owner[T, i][2]) == 1]
            if len(single) > (e - s) / 2 and np.median(single) < 0.6 * width[black[i]]: continue  # thinner than any bar: text, sparks
            if not single:
                # Only ever seen as part of a wider run, in the middle of a neighbour's bar that sticks out on both sides by more than this piece is long: the neighbour's glow, not a bar.
                # ponytail: also drops a real short note under a held neighbour key (a trill on a held note)
                partners = {j for T in range(s, e) for j in owner[T, i][2] if j != i}
                d = max(3, e - s)
                if any(s >= d and on[s - d:s, j].all() and on[e:e + d, j].all() for j in partners): continue
            notes.append((s, e, keys[i][0]))
    log(f'threshold {th}, {len(junk)} pop-ups/flashes skipped, bar widths white {width[False]:.0f} black {width[True]:.0f} px, {len(notes)} notes')
    return sorted(notes)


def write_midi(notes, path, velocity=80):
    """notes: (start s, end s, midi)"""
    ev = []
    for s, e, p in notes:
        ev += [(s, 1, mido.Message('note_on', note=p, velocity=velocity)), (e, 0, mido.Message('note_off', note=p, velocity=0))]
    ev.sort(key=lambda x: (x[0], x[1]))
    out = mido.MidiFile(ticks_per_beat=480); tr = mido.MidiTrack(); out.tracks.append(tr)
    tr.append(mido.MetaMessage('set_tempo', tempo=500000)); last = 0  # 960 ticks per second
    for t, _, msg in ev:
        tick = int(round(max(t, 0) * 960)); tr.append(msg.copy(time=tick - last)); last = tick
    out.save(path)


def note_number(text):
    """'A0', 'C#4', 'Bb3' (middle C is C4) or a MIDI number"""
    m = re.fullmatch(r'([A-Ga-g])([#b]?)(-?\d)', text.strip())
    if not m: return int(text)
    return NAMES.index(m[1].upper()) + {'#': 1, 'b': -1, '': 0}[m[2]] + 12 * (int(m[3]) + 1)


def key_range(spec):
    """'61' or 'C2-C7' -> (lowest, highest) MIDI note"""
    spec = spec.strip()
    if spec in KEYBOARDS: spec = '-'.join(KEYBOARDS[spec])
    m = re.fullmatch(r'(.+?\d)\s*-\s*(.+)', spec)
    if not m: raise ValueError(f'keyboard {spec!r}: use one of {", ".join(KEYBOARDS)} or a range like C2-C7')
    lo, hi = note_number(m[1]), note_number(m[2])
    if hi - lo < 11: raise ValueError(f'keyboard {spec!r} is smaller than an octave')
    return lo, hi


def fold(notes, lo, hi):
    """Move notes outside lo..hi by whole octaves until they fit (same note name, so the harmony stays); a note landing on the same key while it is already down merges into it."""
    moved = [[s, e, p + 12 * max(0, -((p - lo) // 12)) - 12 * max(0, -((hi - p) // 12))] for s, e, p in notes]
    moved.sort(key=lambda n: (n[2], n[0])); out = []
    for n in moved:
        if out and out[-1][2] == n[2] and n[0] <= out[-1][1]: out[-1][1] = max(out[-1][1], n[1])
        else: out.append(n)
    log(f'keyboard {name(lo)}-{name(hi)}: {sum(not lo <= p <= hi for _, _, p in notes)} notes moved by octaves, '
        f'{len(notes) - len(out)} merged into a note already sounding')
    return sorted(tuple(n) for n in out)


def find_mscore():
    for c in MSCORE:
        if c and (shutil.which(c) or os.path.isfile(c)): return shutil.which(c) or c


def soundfont():
    for p in (os.environ.get('YTPIANO2SHEET_SF2', ''), os.path.join(CACHE, os.path.basename(SF2_URL))):
        if p and os.path.isfile(p): return p


def missing(formats):
    """What the requested formats need and this machine lacks: {name: install command, 'download' or None}"""
    brew = sys.platform == 'darwin' and shutil.which('brew'); apt = shutil.which('apt-get')
    pkg = lambda b, a: ['brew', 'install'] + b.split() if brew else ['sudo', 'apt-get', 'install', '-y', a] if apt else None
    need = {}
    if {'pdf', 'mscz'} & formats and not find_mscore(): need['MuseScore'] = pkg('--cask musescore', 'musescore3')
    if {'wav', 'm4a'} & formats:
        if not shutil.which('fluidsynth'): need['fluidsynth'] = pkg('fluid-synth', 'fluidsynth')
        if not soundfont(): need['soundfont'] = 'download'
    if 'm4a' in formats and not shutil.which('ffmpeg'): need['ffmpeg'] = pkg('ffmpeg', 'ffmpeg')
    return need


def describe(need):
    for what, cmd in need.items():
        how = f'download {SF2_URL} to {CACHE}' if cmd == 'download' else ' '.join(cmd) if cmd else f'install by hand: {HINTS[what]}'
        log(f'missing: {what} ({how})')


def install(need):
    import urllib.request
    for what, cmd in need.items():
        if cmd == 'download':
            os.makedirs(CACHE, exist_ok=True); dst = os.path.join(CACHE, os.path.basename(SF2_URL))
            log(f'downloading the soundfont (32 MB) to {dst}')
            urllib.request.urlretrieve(SF2_URL, dst + '.part'); os.replace(dst + '.part', dst)
        elif cmd:
            log('running: ' + ' '.join(cmd)); subprocess.run(cmd, check=True)
        else:
            sys.exit(f'{what} has to be installed by hand: {HINTS[what]}')


def render(notes, stem, formats):
    """Audio of the notes. The bars show key presses, not the sustain pedal, so for listening each note rings on until the next note in the same hand (split at middle C), for at most 2 s."""
    import tempfile
    ring = []
    for right in (True, False):
        hand = [n for n in notes if (n[2] >= 60) == right]; starts = sorted({n[0] for n in hand})
        for s, e, p in hand:
            k = bisect.bisect_right(starts, s + 1e-6); nxt = starts[k] if k < len(starts) else e
            ring.append((s, max(e, min(nxt, s + 2.0)), p))
    wav = stem + '.wav'
    with tempfile.TemporaryDirectory() as tmp:
        write_midi(ring, os.path.join(tmp, 'ring.mid'))
        subprocess.run(['fluidsynth', '-ni', '-g', '0.8', '-r', '44100', '-F', wav, soundfont(), os.path.join(tmp, 'ring.mid')],
                       check=True, capture_output=True)
    if 'm4a' in formats:
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', wav, '-c:a', 'aac', '-b:a', '192k', stem + '.m4a'], check=True)
    if 'wav' not in formats: os.remove(wav)


def ask(question, default):
    """Ask on a terminal; anywhere else (scripts, Claude) take the default."""
    if not (sys.stdin.isatty() and sys.stderr.isatty()): return default
    print(f'{question} [{default}]: ', end='', file=sys.stderr, flush=True)
    try:
        return input().strip() or default
    except EOFError:
        return default


def review(roll, notes, keys, fps, t0, folder, seconds=8):
    """Images of the roll with the notes drawn on, for checking by eye."""
    os.makedirs(folder, exist_ok=True)
    pos = {p: x for p, x, _ in keys}; lo, hi = min(n[2] for n in notes), max(n[2] for n in notes)
    x0, x1 = max(0, int(pos[lo]) - 40), int(pos[hi]) + 40; step = int(seconds * fps); sy = max(1, round(720 / step))
    for k, f0 in enumerate(range(max(0, notes[0][0] - int(fps)), notes[-1][1] + 1, step)):
        img = cv2.cvtColor(cv2.resize(roll[f0:f0 + step, x0:x1], None, fx=1, fy=sy, interpolation=cv2.INTER_NEAREST), cv2.COLOR_GRAY2BGR) // 4 * 3
        for s, e, p in notes:
            if e > f0 and s < f0 + step:
                c = (0, 140, 255) if p % 12 in BLACK else (0, 220, 0); x = int(pos[p]) - x0
                cv2.rectangle(img, (x - 9, (s - f0) * sy), (x + 9, (e - f0) * sy), c, 1)
                cv2.putText(img, name(p), (x - 9, (s - f0) * sy - 2), 0, 0.3, c, 1)
        for sec in range(int((f0 + t0) / fps) + 1, int((f0 + step + t0) / fps) + 1):
            y = int((sec * fps - t0 - f0) * sy); cv2.line(img, (0, y), (10, y), (255, 255, 255), 1)
            cv2.putText(img, f'{sec // 60}:{sec % 60:02d}', (12, y + 4), 0, 0.4, (255, 255, 255), 1)
        cv2.imwrite(os.path.join(folder, f'{k:03d}.png'), img)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('src', nargs='?', help='YouTube (or any yt-dlp) link, or a video file')
    ap.add_argument('-f', '--formats', help=f'comma separated, any of: {", ".join(FORMATS)} (asked when run in a terminal, else mid,musicxml,pdf)')
    ap.add_argument('-k', '--keys', help='fold the notes onto your keyboard: ' + ', '.join(KEYBOARDS) + ' keys, or a range like C2-C7 (middle C = C4); default 88')
    ap.add_argument('-o', '--out', default='out', help='output folder (default: out)')
    ap.add_argument('--install', action='store_true', help='install missing prerequisites without asking')
    ap.add_argument('--check', action='store_true', help='only check the prerequisites for --formats, then exit')
    ap.add_argument('--lowest', help='lowest key shown in the video, e.g. A0 or C2, if its keyboard is not a standard size')
    ap.add_argument('--meter', help='meter for the sheet: 3 or 4 (quarters per bar), or 6/8, 9/8, 12/8 (default: guessed, 3 or 4)')
    ap.add_argument('--key', help='key for the sheet, e.g. D, or d for D minor (default: guessed)')
    ap.add_argument('--start', type=float, default=0, help='skip the first SECONDS of the video (an intro, logo or title card)')
    ap.add_argument('--end', type=float, help='stop at SECONDS of the video (an outro, end card or credits)')
    ap.add_argument('--threshold', type=int, help='brightness a bar must reach (default: measured); lower it when dark bars are missed')
    a = ap.parse_args()
    if not a.src and not a.check: ap.error('give a link or a video file')
    formats = {f.strip().lower().lstrip('.') for f in (a.formats or ask('Outputs, comma separated (' + ', '.join(FORMATS) + ')', 'mid,musicxml,pdf')).split(',') if f.strip()}
    formats = {'mid' if f in ('midi', 'mid') else 'musicxml' if f in ('xml', 'mxl') else f for f in formats}
    if formats - set(FORMATS): ap.error(f'unknown format(s) {", ".join(sorted(formats - set(FORMATS)))}; pick from {", ".join(FORMATS)}')
    need = missing(formats)
    if a.check:
        describe(need); log('all prerequisites present' if not need else 'rerun with --install to install them')
        sys.exit(1 if need else 0)
    try:
        lo, hi = key_range(a.keys or ask('Your keyboard: number of keys (' + ', '.join(KEYBOARDS) + ') or a range like C2-C7', '88'))
    except ValueError as err:
        ap.error(str(err))
    if need:
        describe(need)
        if not (a.install or ask('Install them now? (y/n)', 'n').lower().startswith('y')):
            sys.exit('stopped: missing prerequisites. Rerun with --install, or leave out the formats that need them.')
        install(need)
        if missing(formats): describe(missing(formats)); sys.exit('still missing after installing')

    path, title = fetch(a.src, os.path.join(CACHE, 'videos'))
    # One folder per song. yt-dlp writes | : ? as fullwidth in file names, so a cached file and its link name the same folder
    song = re.sub(r'\s+', ' ', re.sub(r'[\\/:*?"<>|｜：？＂／＼＊＜＞]', '', title)).strip()
    folder = os.path.join(a.out, song); os.makedirs(folder, exist_ok=True)
    stem = os.path.join(folder, song + ('' if (lo, hi) == (21, 108) else f' ({hi - lo + 1} keys)'))
    log(f'video: {path}')
    cap = cv2.VideoCapture(path); fps = cap.get(cv2.CAP_PROP_FPS)
    still = np.median(sample_frames(cap), 0).astype(np.uint8)
    try:
        keys, top = keyboard(still, note_number(a.lowest) if a.lowest else None)
    except (StopIteration, ValueError, IndexError):
        sys.exit('could not find a piano keyboard in the video: it needs a keyboard with bars falling onto it (or rising from it)')
    log(f'keyboard in the video: {len(keys)} keys {name(keys[0][0])}-{name(keys[-1][0])}, top at row {top}, {fps:g} fps')
    ys = bar_rows(still, top); bg = still[ys].astype(int)
    try:
        v = bar_speed(cap, fps, ys, bg)
    except AssertionError as err:
        sys.exit(str(err))
    roll, t0 = read_roll(cap, ys, bg, v, top)
    cut = max(0, int(a.start * fps) - t0); roll, t0 = roll[cut:], t0 + cut
    if a.end: roll = roll[:max(1, int(a.end * fps) - t0)]
    keys = fit_keys(roll, keys)
    frames = bars_to_notes(roll, keys, fps, a.threshold)
    if not frames: sys.exit('found the keyboard but no bars reaching it: no notes to write')
    notes = [((s + t0) / fps, (e + t0) / fps, p) for s, e, p in frames]
    if (lo, hi) != (21, 108) or not all(lo <= p <= hi for _, _, p in notes):
        notes = fold(notes, lo, hi)
    written = []
    if 'mid' in formats:
        write_midi(notes, stem + '.mid'); written.append(stem + '.mid')
    if 'review' in formats:
        review(roll, frames, keys, fps, t0, os.path.join(folder, 'review')); written.append(os.path.join(folder, 'review/'))
    if {'musicxml', 'pdf', 'mscz'} & formats:
        import sheet
        written += sheet.write([(s, e, p, 80) for s, e, p in notes], stem, title, formats, find_mscore(), a.meter, a.key)
    if {'wav', 'm4a'} & formats:
        render(notes, stem, formats); written += [f'{stem}.{f}' for f in ('wav', 'm4a') if f in formats]
    log('wrote:\n  ' + '\n  '.join(written))


if __name__ == '__main__':
    main()
