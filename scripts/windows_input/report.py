"""Cases and exact observer expectations; no desktop calls on import."""
import re

ESC, CTRL, SHIFT, ALT = 27, 17, 16, 18
MODES = ('native', 'legacy', 'kitty1', 'kitty5', 'kitty9')
CASES = [
    ('escape-ctrl-c', [[ESC], [CTRL, 67]]),
    ('escape', [[ESC]]), ('escape-repeat', [[ESC], [ESC]]),
    ('escape-letter', [[ESC], [65]]), ('escape-space', [[ESC], [32]]),
    ('shift-escape', [[SHIFT, ESC]]), ('ctrl-c', [[CTRL, 67]]),
    ('shift-enter', [[SHIFT, 13]]), ('ctrl-enter', [[CTRL, 13]]),
    ('alt-enter', [[ALT, 13]]), ('shift-tab', [[SHIFT, 9]]),
    ('letter', [[65]]), ('shift-letter', [[SHIFT, 65]]),
    ('ctrl-letter', [[CTRL, 65]]), ('alt-letter', [[ALT, 65]]),
]


def semantic(chord):
    key = chord[-1]
    mods = (1 if SHIFT in chord[:-1] else 0) | (2 if ALT in chord[:-1] else 0) | (4 if CTRL in chord[:-1] else 0)
    char = key if key < 65 else key + (0 if mods & 1 else 32)
    if mods & 4 and key >= 65:
        char = key - 64
    if mods & 4 and key == 13:
        char = 10
    return key, mods, char


def vt_expected(mode, gestures):
    out = b''
    for chord in gestures:
        key, mods, char = semantic(chord)
        code = key + 32 if key >= 65 else key
        if mode.startswith('kitty') and (mods & 6 or mods and key < 65 or key == ESC or mode == 'kitty9'):
            out += f'\x1b[{code}{";" + str(mods + 1) if mods else ""}u'.encode()
        elif key == 9 and mods == 1:
            out += b'\x1b[Z'
        else:
            out += (b'\x1b' if mods & 2 else b'') + bytes([char])
    return out


def assess(mode, gestures, capture):
    if capture.get('error') or not capture.get('quiet_reached'):
        return 'observer error or capture did not settle'
    if mode != 'native':
        actual = bytes.fromhex(capture['hex'])
        expected = vt_expected(mode, gestures)
        return None if actual == expected else f'expected {expected.hex()}, got {actual.hex()}'
    # [type, down, repeat, vk, scan, unicode, control]. Ignore modifier keys
    # and resize/focus, but reject unknown/literal synthetic input and repeats.
    if any(r[0] == 1 and r[3] == 3 for r in capture['records']):
        return 'unexpected VK_CANCEL'
    records = [r for r in capture['records'] if r[0] == 1 and r[1] and r[3] not in (16, 17, 18, 160, 161, 162, 163, 164, 165)]
    expected = [semantic(chord) for chord in gestures]
    actual = [(r[3], (1 if r[6] & 16 else 0) | (2 if r[6] & 3 else 0) | (4 if r[6] & 12 else 0), r[5]) for r in records]
    if any(r[2] != 1 for r in records) or actual != expected:
        return f'expected native {expected}, got {actual}'
    return None


def negotiations(data):
    return [m.group().decode('ascii') for m in re.finditer(rb'\x1b\[(?:\?[0-9;]*[hl]|[><=][0-9;]*u|\?u)', data)]
