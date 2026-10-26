#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = ["numpy", "opencv-python-headless", "mido"]
# ///
"""Self-test: draw a falling-bars video with known notes, read it back, compare. Also checks the keyboard fold."""
import os, sys, tempfile
import cv2, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ytpiano2sheet as B

W, H, FPS, V, TOP = 1280, 720, 30, 5.0, 600          # frame size, fps, bar speed px/frame, keyboard top row
WW = W / 52                                           # white key width; 88 keys, A0 at the left edge
NOTES = [  # (start s, length s, midi): a tune plus the hard cases
    (1.0, 0.5, 62), (1.5, 0.5, 64), (2.0, 0.5, 66), (2.5, 1.0, 67),             # stepwise, bars end to start
    (1.0, 1.5, 38), (1.0, 1.5, 45), (2.5, 1.0, 43),                              # bass
    (3.5, 0.6, 62), (3.5, 0.6, 64),                                              # adjacent whites at once
    (4.2, 0.6, 73), (4.2, 0.6, 74),                                              # black and white neighbours at once
    (5.0, 0.2, 69), (5.3, 0.2, 69), (5.6, 0.2, 69),                              # repeated note
    (6.0, 0.1, 70), (6.2, 1.2, 54), (6.2, 1.2, 58), (6.2, 1.2, 61),              # short black note, black chord
    (7.5, 0.5, 24), (7.5, 0.5, 100), (8.2, 0.8, 21), (8.2, 0.8, 108),            # keyboard ends
    (9.0, 2 / FPS, 83), (9.0 + 2 / FPS, 2 / FPS, 82), (9.0 + 4 / FPS, 2 / FPS, 84), (9.0 + 6 / FPS, 2 / FPS, 81),  # 2-frame notes, end to start
]


def white_index(p):  # white keys counted from A0
    return sum(1 for q in range(21, p) if q % 12 not in B.BLACK)


def key_x(p):
    if p % 12 not in B.BLACK: return (white_index(p) + 0.5) * WW
    off = {1: -0.12, 3: 0.12, 6: -0.15, 8: 0.0, 10: 0.15}[p % 12]  # black keys sit a little off the boundary, as on a piano
    return (white_index(p - 1) + 1 + off) * WW


def frame(f, rng):
    img = np.full((H, W, 3), 8, np.uint8)
    t = f / FPS
    for s, d, p in NOTES:                              # bars: bottom reaches TOP at the onset
        bottom = TOP - (s - t) * FPS * V; top = bottom - d * FPS * V
        if bottom < 0 or top > TOP: continue
        w = (0.85 if p % 12 not in B.BLACK else 0.55) * WW; x = key_x(p)
        cv2.rectangle(img, (int(x - w / 2), int(max(top, 0))), (int(x + w / 2), int(min(bottom, TOP - 1))), (70, 90, 255), -1)
    for s in (2.0, 4.0, 6.0):                          # bar lines: thin, full width, moving with the bars across held notes
        y = int(TOP - (s - t) * FPS * V)
        if 0 <= y < TOP - 2: img[y:y + 2] = 200
    if t < 0.2: img[:TOP] = 230                        # a flash over the whole bar area
    cv2.putText(img, 'TUTORIAL', (W - 260, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (230, 230, 230), 3)   # static overlay
    for _ in range(60):                                # particles
        y, x = rng.integers(0, TOP), rng.integers(0, W); cv2.circle(img, (int(x), int(y)), 2, (60, 140, 255), -1)
    img[TOP:] = 225                                    # keyboard
    img[TOP:TOP + 3] = (40, 120, 255)                  # glow line on the keys
    for k in range(1, 52): img[TOP + 3:, int(k * WW)] = 70
    for p in range(21, 109):
        if p % 12 in B.BLACK:
            x = key_x(p); img[TOP + 3:TOP + 75, int(x - 0.3 * WW):int(x + 0.3 * WW)] = 25
    return img


def main():
    rng = np.random.default_rng(0)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'synthetic.mp4')
        out = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*'mp4v'), FPS, (W, H))
        for f in range(int(10 * FPS)): out.write(frame(f, rng))
        out.release()
        cap = cv2.VideoCapture(path)
        still = np.median(B.sample_frames(cap), 0).astype(np.uint8)
        keys, top = B.keyboard(still)
        assert (len(keys), keys[0][0], keys[-1][0]) == (88, 21, 108), f'keyboard read as {len(keys)} keys {keys[0][0]}-{keys[-1][0]}'
        err = max(abs(x - key_x(p)) for p, x, _ in keys)
        assert err < 0.25 * WW, f'key positions off by up to {err:.1f} px'
        ys = B.bar_rows(still, top); bg = still[ys].astype(int)
        v = B.bar_speed(cap, FPS, ys, bg)
        assert abs(v - V) < 0.05, f'bar speed {v}'
        v2 = B.bar_speed(cap, FPS, ys, bg, seconds=8.35)   # 250 frames, not a multiple of 3: once biased every lag by a frame
        assert abs(v2 - V) < 0.05, f'bar speed {v2} on a 250-frame window'
        roll, t0 = B.read_roll(cap, ys, bg, v, top)
        got = [((s + t0) / FPS, (e + t0) / FPS, p) for s, e, p in B.bars_to_notes(roll, keys, FPS)]
    # Onsets are measured at the keyboard band's top, a fixed few frames from TOP: take the best common offset
    want = sorted((s, s + d, p) for s, d, p in NOTES)
    def match(off):
        return [(w, min((g for g in got if g[2] == w[2] and abs(g[0] + off - w[0]) < 1.5 / FPS), default=None, key=lambda g: abs(g[0] + off - w[0]))) for w in want]
    off = max(np.arange(-0.5, 0.5, 1 / FPS / 4), key=lambda o: sum(g is not None for _, g in match(o)))
    pairs = match(off)
    missing = [w for w, g in pairs if g is None]
    extra = [g for g in got if not any(g is h for _, h in pairs)]
    long_err = max(abs((g[1] - g[0]) - (w[1] - w[0])) for w, g in pairs if g)
    print(f'{len(want)} notes drawn, {len(got)} read, missing {missing}, extra {extra}, worst length error {long_err * FPS:.1f} frames')
    assert not missing and not extra and long_err < 2.5 / FPS

    # A pop-up lit across more than 6 white keys is boxed (with a margin, it is long); separate bars of a chord are not
    m = np.zeros((100, W), bool); m[40:60, 100:400] = True
    for k in range(6): m[:, int((20 + k) * WW + 2):int((21 + k) * WW - 2)] = True
    assert B.junk_boxes(m, WW, FPS) == [(25, 75, int(100 - WW), int(400 + WW))], B.junk_boxes(m, WW, FPS)

    # Keyboard fold onto 61 keys: everything inside C2-C7, same note names
    lo, hi = B.key_range('61')
    folded = B.fold([(s, s + d, p) for s, d, p in NOTES], lo, hi)
    assert (lo, hi) == (36, 96) and all(lo <= p <= hi for _, _, p in folded)
    assert sorted({p % 12 for _, _, p in folded}) == sorted({p % 12 for _, _, p in NOTES})
    assert B.key_range('C3-C5') == (48, 72) and B.note_number('Bb3') == 58 and B.note_number('A0') == 21
    print('SELFTEST OK')


if __name__ == '__main__':
    main()
