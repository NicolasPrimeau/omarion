#!/usr/bin/env python3
"""
Combine two half-height asciinema casts into a single split-pane cast.
nova plays in the top pane, orion in the bottom pane.
Usage: artel-demo-combine.py nova.cast orion.cast out.cast
"""

import json
import sys
import time

import pyte

COLS = 110
PANE_H = 18  # each pane height
BORDER = 1  # one-row border between panes
TOTAL_H = PANE_H * 2 + BORDER  # 37

NOVA_LABEL = "  nova  "
ORION_LABEL = "  orion  "
BORDER_COLOR = "\x1b[38;5;59m"  # dim grey
LABEL_COLOR = "\x1b[38;5;180m"  # muted gold
RESET = "\x1b[0m"

SNAPSHOT_INTERVAL = 0.12  # seconds between combined frames
TITLE_DURATION = 10.0  # seconds each act title is shown (video is 2.5x accelerated)

CYAN = "\x1b[38;5;51m"
GOLD = "\x1b[38;5;220m"
WHITE = "\x1b[97m"
DIM = "\x1b[2m"
BOLD = "\x1b[1m"
RESET = "\x1b[0m"

ACTS = [
    ("ACT I", "THE SUPPORT AGENT SPOTS A PATTERN"),
    ("ACT II", "THE PRODUCT AGENT MAKES THE CALL"),
    ("ACT III", "DECISION IN SHARED MEMORY"),
]


def _title_card(act_label: str, act_title: str) -> str:
    mid = TOTAL_H // 2
    rule = "─" * COLS

    act_plain = act_label
    title_plain = act_title
    act_pad = (COLS - len(act_plain)) // 2
    title_pad = (COLS - len(title_plain)) // 2

    act_line = f"{BOLD}{GOLD}{' ' * act_pad}{act_plain}{RESET}"
    title_line = f"{BOLD}{WHITE}{' ' * title_pad}{title_plain}{RESET}"
    rule_line = f"{DIM}{rule}{RESET}"

    out = "\x1b[2J\x1b[H"
    out += f"\x1b[{mid - 2};1H{rule_line}"
    out += f"\x1b[{mid - 1};1H"  # blank breathing room
    out += f"\x1b[{mid};1H{act_line}"
    out += f"\x1b[{mid + 1};1H{title_line}"
    out += f"\x1b[{mid + 2};1H"  # blank breathing room
    out += f"\x1b[{mid + 3};1H{rule_line}"
    return out


CREDITS_DURATION = 10.0  # seconds credits are shown


def _credits_card() -> str:
    mid = TOTAL_H // 2
    rule = "─" * COLS
    rule_line = f"{DIM}{rule}{RESET}"

    lines = [
        f"{BOLD}{GOLD}{'directed by':^{COLS}}{RESET}",
        f"{BOLD}{WHITE}{'Claudin Tarantino':^{COLS}}{RESET}",
        "",
        f"{DIM}{'artel.ai':^{COLS}}{RESET}",
    ]

    out = "\x1b[2J\x1b[H"
    out += f"\x1b[{mid - 3};1H{rule_line}"
    for i, line in enumerate(lines):
        out += f"\x1b[{mid - 1 + i};1H{line}"
    out += f"\x1b[{mid + len(lines) + 1};1H{rule_line}"
    return out


def load_cast(path):
    with open(path) as f:
        lines = f.readlines()
    header = json.loads(lines[0])
    events = [json.loads(line) for line in lines[1:]]
    return header, events


def build_screen(width, height, events):
    """Replay events through pyte and return the final Screen."""
    screen = pyte.Screen(width, height)
    stream = pyte.ByteStream(screen)
    for _t, kind, data in events:
        if kind == "o":
            stream.feed(data.encode("utf-8", errors="replace"))
    return screen


def screen_to_ansi(screen, row_offset=0, cols=COLS):
    """Convert a pyte Screen to ANSI escape sequence string."""
    parts = []
    for y in range(screen.lines):
        row = screen.buffer[y]
        parts.append(f"\x1b[{y + 1 + row_offset};1H")
        prev_fg = prev_bg = prev_bold = None
        for x in range(cols):
            char = row[x]
            bold = char.bold
            fg = char.fg
            bg = char.bg

            codes = []
            if bold != prev_bold:
                codes.append("1" if bold else "22")
                prev_bold = bold
            if fg != prev_fg:
                if fg == "default":
                    codes.append("39")
                elif fg.startswith("#") or len(fg) == 6:
                    try:
                        r, g, b = int(fg[0:2], 16), int(fg[2:4], 16), int(fg[4:6], 16)
                        codes.append(f"38;2;{r};{g};{b}")
                    except ValueError:
                        codes.append("39")
                else:
                    codes.append("39")
                prev_fg = fg
            if bg != prev_bg:
                if bg == "default":
                    codes.append("49")
                prev_bg = bg

            if codes:
                parts.append(f"\x1b[{';'.join(codes)}m")
            parts.append(char.data if char.data else " ")

        parts.append("\x1b[0m")
    return "".join(parts)


def border_line():
    """One-row separator between panes."""
    half = (COLS - len(NOVA_LABEL) - len(ORION_LABEL) - 4) // 2
    line = (
        BORDER_COLOR
        + "─" * half
        + RESET
        + LABEL_COLOR
        + NOVA_LABEL
        + RESET
        + BORDER_COLOR
        + "─" * (COLS - half * 2 - len(NOVA_LABEL) - len(ORION_LABEL) - 4)
        + RESET
        + LABEL_COLOR
        + ORION_LABEL
        + RESET
        + BORDER_COLOR
        + "─" * half
        + RESET
    )
    return f"\x1b[{PANE_H + 1};1H" + line


def combine(nova_cast, orion_cast, out_cast):
    nova_hdr, nova_events = load_cast(nova_cast)
    orion_hdr, orion_events = load_cast(orion_cast)

    nova_duration = nova_events[-1][0] if nova_events else 0
    # Each section is preceded by a title card
    act1_start = TITLE_DURATION
    act2_start = act1_start + nova_duration + TITLE_DURATION
    orion_offset = act2_start + TITLE_DURATION  # orion content starts after ACT II card

    # ACT III fires when orion is ~70% through its content
    orion_duration = orion_events[-1][0] if orion_events else 0
    act3_t = orion_offset + orion_duration * 0.65

    credits_start = orion_offset + orion_duration + 2.0
    total_end = credits_start + CREDITS_DURATION

    combined = []
    t = 0.0

    nova_screen = pyte.Screen(COLS, PANE_H)
    orion_screen = pyte.Screen(COLS, PANE_H)
    nova_stream = pyte.ByteStream(nova_screen)
    orion_stream = pyte.ByteStream(orion_screen)

    nova_idx = 0
    orion_idx = 0
    prev_frame = None
    act3_shown = False

    while t <= total_end:
        # Title card windows — freeze content and show overlay
        if t < act1_start:
            frame = _title_card(*ACTS[0])
            combined.append((round(t, 4), "o", frame))
            t = round(t + SNAPSHOT_INTERVAL, 4)
            continue

        act2_card_end = act2_start + TITLE_DURATION
        if act2_start <= t < act2_card_end:
            frame = _title_card(*ACTS[1])
            combined.append((round(t, 4), "o", frame))
            t = round(t + SNAPSHOT_INTERVAL, 4)
            continue

        # Advance nova events (only during act I window)
        nova_t = t - act1_start
        while nova_idx < len(nova_events) and nova_events[nova_idx][0] <= nova_t:
            kind, data = nova_events[nova_idx][1], nova_events[nova_idx][2]
            if kind == "o":
                nova_stream.feed(data.encode("utf-8", errors="replace"))
            nova_idx += 1

        # Advance orion events (time-shifted to after act II card)
        orion_t = t - orion_offset
        while orion_idx < len(orion_events) and orion_events[orion_idx][0] <= orion_t:
            kind, data = orion_events[orion_idx][1], orion_events[orion_idx][2]
            if kind == "o":
                orion_stream.feed(data.encode("utf-8", errors="replace"))
            orion_idx += 1

        # ACT III title card overlay (brief, overlaid on live content)
        if not act3_shown and t >= act3_t:
            act3_card_end = act3_t + TITLE_DURATION
            if t < act3_card_end:
                frame = _title_card(*ACTS[2])
                combined.append((round(t, 4), "o", frame))
                t = round(t + SNAPSHOT_INTERVAL, 4)
                continue
            else:
                act3_shown = True

        # Build combined frame
        frame = (
            "\x1b[2J\x1b[H"  # clear + home
            + screen_to_ansi(nova_screen, row_offset=0)
            + border_line()
            + screen_to_ansi(orion_screen, row_offset=PANE_H + BORDER)
        )

        if t >= credits_start:
            frame = _credits_card()
            combined.append((round(t, 4), "o", frame))
            t = round(t + SNAPSHOT_INTERVAL, 4)
            continue

        if frame != prev_frame:
            combined.append((round(t, 4), "o", frame))
            prev_frame = frame

        t = round(t + SNAPSHOT_INTERVAL, 4)

    # Cap gaps
    def cap_gaps(events, max_gap=2.0):
        result, offset, prev = [], 0.0, None
        for ev_t, kind, data in events:
            adj = ev_t - offset
            if prev is not None and adj - prev > max_gap:
                offset += adj - prev - max_gap
                adj = ev_t - offset
            result.append((round(adj, 4), kind, data))
            prev = adj
        return result

    combined = cap_gaps(combined)
    if combined:
        t0 = combined[0][0]
        combined = [(round(t - t0, 4), k, d) for t, k, d in combined]

    header = {
        "version": 2,
        "width": COLS,
        "height": TOTAL_H,
        "timestamp": int(time.time()),
        "title": "Artel — two Claude Code agents, one shared brain",
        "env": {"SHELL": "/bin/bash", "TERM": "xterm-256color"},
    }
    if combined:
        header["duration"] = combined[-1][0] + 2.0

    with open(out_cast, "w") as f:
        f.write(json.dumps(header) + "\n")
        for row in combined:
            f.write(json.dumps(list(row)) + "\n")

    dur = combined[-1][0] if combined else 0
    print(f"combined: {len(combined)} frames, {dur:.0f}s", flush=True)


if __name__ == "__main__":
    nova_cast = sys.argv[1] if len(sys.argv) > 1 else "/tmp/artel-nova.cast"
    orion_cast = sys.argv[2] if len(sys.argv) > 2 else "/tmp/artel-orion.cast"
    out_cast = sys.argv[3] if len(sys.argv) > 3 else "/tmp/artel-demo.cast"
    combine(nova_cast, orion_cast, out_cast)
