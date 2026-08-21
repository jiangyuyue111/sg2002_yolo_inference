"""
terminal_debug.py — Pure terminal visual debugger for the hunter state machine.

No GUI dependencies — just Python stdlib. Renders everything in the terminal
using ANSI escape codes and Unicode block characters.

Controls:
  w/a/s/d — move ball
  q/e     — make ball bigger/smaller
  r       — toggle red bucket
  space   — pause/resume
  0       — reset
  1-5     — preset positions

Usage:
  python pipeline/terminal_debug.py
"""

import sys
import os
import time
import threading
import signal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.state_machine import (
    HunterStateMachine, TargetInfo, State, RobotGeometry,
)

FRAME_W, FRAME_H = 640, 480

# ── ANSI / terminal helpers ──────────────────────────────────────────

CSI = '\033['

def cursor_home():
    sys.stdout.write(CSI + 'H')

def clear_screen():
    sys.stdout.write(CSI + '2J' + CSI + 'H')

def hide_cursor():
    sys.stdout.write(CSI + '?25l')

def show_cursor():
    sys.stdout.write(CSI + '?25h')

def goto(x, y):
    sys.stdout.write(f'{CSI}{y};{x}H')

def color_bg(r, g, b, text=' '):
    return f'{CSI}48;2;{r};{g};{b}m{text}{CSI}0m'

def color_fg(r, g, b, text=''):
    return f'{CSI}38;2;{r};{g};{b}m{text}{CSI}0m'

def color_all(fg_r, fg_g, fg_b, bg_r, bg_g, bg_b, text=''):
    return f'{CSI}38;2;{fg_r};{fg_g};{fg_b};48;2;{bg_r};{bg_g};{bg_b}m{text}{CSI}0m'

# State colors
STATE_CLR = {
    State.CHASE_TENNIS:    (249, 115, 22),   # orange
    State.POSITION_TENNIS: (59, 130, 246),   # blue
    State.GRAB_TENNIS:     (34, 197, 94),    # green
    State.CHASE_BUCKET:    (139, 92, 246),   # purple
    State.RELEASE_TENNIS:  (239, 68, 68),    # red
}


class TerminalRenderer:
    """Renders the hunter state in a terminal using a double-buffered approach."""

    def __init__(self):
        self.view_w = 80
        self.view_h = 30
        self._buf = []
        self._last_lines = 0

    def _rect(self, x, y, w, h, clr):
        """Draw filled rectangle in viewport coords."""
        r, g, b = clr
        for dy in range(h):
            row = color_bg(r, g, b, '  ') * w
            goto(x * 2, y + dy)
            sys.stdout.write(row)

    def _text(self, x, y, text, fg=(226, 232, 240)):
        r, g, b = fg
        goto(x * 2, y)
        sys.stdout.write(color_fg(r, g, b, text))

    def _hline(self, y, ch='─', clr=(55, 65, 81)):
        r, g, b = clr
        goto(0, y)
        sys.stdout.write(color_fg(r, g, b, ch * 80))

    def _vline(self, x, y, h, ch='│', clr=(55, 65, 81)):
        r, g, b = clr
        for dy in range(h):
            goto(x * 2, y + dy)
            sys.stdout.write(color_fg(r, g, b, ch))

    def _bar_h(self, x, y, w, val, max_val, clr_pos=(34, 197, 94), clr_neg=(239, 68, 68)):
        """Horizontal bar. val in [-max_val, max_val]."""
        half = w // 2
        center = x + half
        blocks = int(abs(val) / max_val * half)
        r, g, b = clr_pos if val >= 0 else clr_neg
        for i in range(half):
            if val >= 0:
                filled = i < blocks
                bg = (r, g, b) if filled else (30, 41, 59)
            else:
                filled = i < blocks
                bg = (r, g, b) if filled else (30, 41, 59)
                x_pos = center - 1 - i
                goto(x_pos * 2, y)
                sys.stdout.write(color_bg(*bg, '  '))
                continue

            if val >= 0:
                goto((center + i) * 2, y)
                sys.stdout.write(color_bg(*bg, '  '))
        # Center marker
        goto(center * 2, y)
        sys.stdout.write(color_fg(148, 163, 184, '│'))

    def render(self, sim, out, dbg):
        """Render full frame."""
        w = self.view_w
        h = self.view_h
        clear_screen()

        # ── Title bar ──
        title = '  SG2002 HUNTER — TERMINAL DEBUG  '
        goto((w - len(title)) // 2, 1)
        sys.stdout.write(color_all(255, 255, 255, 15, 23, 42, title))

        # ── Left: camera view (scaled 40×24 chars from 640×480) ──
        vx, vy = 1, 3
        vw, vh = 40, 24
        x_scale = vw / FRAME_W
        y_scale = vh / FRAME_H

        # Background
        for dy in range(vh):
            goto(vx * 2, vy + dy)
            sys.stdout.write(color_bg(10, 10, 10, '  ' * vw))

        # 9-grid lines
        g = fsm.geom
        for x_frac in [g.left_boundary, g.right_boundary]:
            gx = int(vx + x_frac * vw)
            for dy in range(vh):
                goto(gx * 2, vy + dy)
                sys.stdout.write(color_fg(55, 65, 81, '┆'))

        for y_frac in [g.top_boundary, g.bottom_boundary]:
            gy = int(vy + y_frac * vh)
            goto(vx * 2, gy)
            sys.stdout.write(color_fg(55, 65, 81, '┄' * vw))

        # Grab zone
        gzx = int(vx + (g.x_left_grab / FRAME_W) * vw)
        gzw = int(((g.x_right_grab - g.x_left_grab) / FRAME_W) * vw)
        gzy = int(vy + g.bottom_boundary * vh)
        gzh = vh - gzy + vy
        for dy in range(gzh):
            goto(gzx * 2, gzy + dy)
            sys.stdout.write(color_bg(20, 50, 20, '  ') * gzw)

        # Ball
        if sim.has_ball:
            bx = int(vx + sim.ball_x * x_scale)
            by = int(vy + sim.ball_y * y_scale)
            br = max(1, int(sim.ball_r * x_scale))
            for dy in range(-br, br + 1):
                for dx in range(-br, br + 1):
                    if dx*dx + dy*dy <= br*br:
                        px, py = bx + dx, by + dy
                        if vx <= px < vx + vw and vy <= py < vy + vh:
                            goto(px * 2, py)
                            sys.stdout.write(color_bg(250, 204, 21, '  '))

        # Detection box
        if out.box_w > 0:
            box_x = int(vx + out.box_x * x_scale)
            # Compute Y from ball position (box_y not stored in ControlOutput)
            est_y = sim.ball_y - sim.ball_r if sim.has_ball else out.frame_h * 0.4
            box_y = int(vy + est_y * y_scale)
            box_w = max(2, int(out.box_w * x_scale))
            box_h = max(1, int(out.box_h * y_scale))
            for dx in range(box_w):
                for dy in range(box_h):
                    if dx == 0 or dx == box_w - 1 or dy == 0 or dy == box_h - 1:
                        px, py = box_x + dx, box_y + dy
                        if vx <= px < vx + vw and vy <= py < vy + vh:
                            goto(px * 2, py)
                            sys.stdout.write(color_fg(34, 197, 94, '██'))

        # Bucket
        if sim.has_bucket:
            bux = int(vx + sim.bucket_x * x_scale)
            buy = int(vy + sim.bucket_y * y_scale)
            buw = max(2, int(sim.bucket_w * x_scale))
            buh = max(2, int(sim.bucket_h * y_scale))
            for dx in range(buw):
                for dy in range(buh):
                    if dx < 1 or dx >= buw - 1 or dy < 1 or dy >= buh - 1:
                        px, py = bux + dx, buy + dy
                        if vx <= px < vx + vw and vy <= py < vy + vh:
                            goto(px * 2, py)
                            sys.stdout.write(color_fg(239, 68, 68, '██'))

        # ── Right panel ──
        px = vx + vw + 2
        py = vy

        # State
        clr = STATE_CLR.get(out.state, (255, 255, 255))
        self._text(px, py, '┌─ STATE ───────┐')
        self._text(px, py + 1, f'│ {out.state.upper():13s} │', clr)
        self._text(px, py + 2, '└──────────────┘')

        py += 4
        # Info rows
        self._text(px, py, f'box:  x={out.box_x:7.0f}  w={out.box_w:7.0f}', (148, 163, 184))
        py += 1
        if sim.has_ball:
            self._text(px, py, f'ball: x={sim.ball_x:7.0f}  y={sim.ball_y:7.0f}  r={sim.ball_r:.0f}', (148, 163, 184))
        else:
            self._text(px, py, f'ball: (hidden)', (100, 116, 139))
        py += 1
        self._text(px, py, f'grab:   {out.grab_confirm}/{fsm.geom.grab_confirm_frames}', (148, 163, 184))
        py += 1
        self._text(px, py, f'time:   {fsm.elapsed_in_state:.1f}s in state', (148, 163, 184))

        py += 2
        # Motor
        self._text(px, py, '─ MOTOR ───────')
        py += 1
        self._text(px, py, f'  L')
        self._bar_h(px + 4, py, 12, int(out.car_left), 100)
        self._text(px + 16, py, f'{out.car_left:+.0f}')
        py += 1
        self._text(px, py, f'  R')
        self._bar_h(px + 4, py, 12, int(out.car_right), 100)
        self._text(px + 16, py, f'{out.car_right:+.0f}')

        py += 2
        # Action
        if out.arm_action:
            self._text(px, py, f'>>> {out.arm_action.upper()}! <<<',
                       (34, 197, 94) if out.arm_action == 'grab' else (59, 130, 246))

        py += 2
        # History
        self._text(px, py, '─ HISTORY ─────')
        py += 1
        for i, s in enumerate(dbg.history[-20:]):
            clr_s = STATE_CLR.get(s, (148, 163, 184))
            r, g_c, b = clr_s
            bar = '█' if i == len(dbg.history[-20:]) - 1 else '▌'
            goto(px * 2, py + i // 2)
            sys.stdout.write(color_fg(r, g_c, b, bar) + ' ' + s[:12])

        # ── Bottom bar ──
        bottom_y = vy + vh + 1
        self._hline(bottom_y)
        self._text(1, bottom_y + 1,
                   f'FPS: {dbg.fps:.0f}  |  '
                   f'w/a/s/d=move  q/e=resize  r=bucket  space=pause  1-5=presets  0=reset  ctrl+c=quit',
                   (100, 116, 139))

        # Flush
        sys.stdout.write(CSI + 'J')  # clear to end
        sys.stdout.flush()
        self._last_lines = bottom_y + 3


class SimWorld:
    def __init__(self):
        self.ball_x = 320.0
        self.ball_y = 240.0
        self.ball_r = 30.0
        self.has_ball = True
        self.bucket_x = 0.0
        self.bucket_y = 0.0
        self.bucket_w = 300.0
        self.bucket_h = 250.0
        self.has_bucket = False

    def get_target(self) -> TargetInfo:
        if self.has_bucket:
            return TargetInfo(has_target=True,
                              x=self.bucket_x, y=self.bucket_y,
                              w=self.bucket_w, h=self.bucket_h, confidence=0.80)
        if not self.has_ball:
            return TargetInfo()
        w = self.ball_r * 2.5
        return TargetInfo(has_target=True,
                          x=self.ball_x - w / 2, y=self.ball_y - w / 2,
                          w=w, h=w, confidence=0.92)

    def move(self, dx, dy):
        self.ball_x = max(20, min(FRAME_W - 20, self.ball_x + dx))
        self.ball_y = max(20, min(FRAME_H - 20, self.ball_y + dy))


class DebugState:
    def __init__(self):
        self.running = True
        self.paused = False
        self.step = False
        self.fps = 0.0
        self.history = []


# ── Keyboard input thread ──

def key_input(sim, dbg):
    """Non-blocking keyboard input (Unix only)."""
    if os.name == 'nt':
        import msvcrt
        while dbg.running:
            if not msvcrt.kbhit():
                time.sleep(0.03)
                continue
            ch = msvcrt.getch()
            _handle_key(ch, sim, dbg)
    else:
        import tty, termios
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        try:
            while dbg.running:
                import select
                if select.select([sys.stdin], [], [], 0.03)[0]:
                    ch = sys.stdin.read(1)
                    _handle_key(ch, sim, dbg)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _handle_key(ch, sim, dbg):
    if isinstance(ch, bytes):
        ch = ch.decode('latin-1', errors='replace')

    speed = 15
    if ch == 'w':
        sim.move(0, -speed)
    elif ch == 's':
        sim.move(0, speed)
    elif ch == 'a':
        sim.move(-speed, 0)
    elif ch == 'd':
        sim.move(speed, 0)
    elif ch == 'q':
        sim.ball_r = min(200, sim.ball_r + 5)
    elif ch == 'e':
        sim.ball_r = max(10, sim.ball_r - 5)
    elif ch == 'r':
        sim.has_bucket = not sim.has_bucket
        sim.has_ball = not sim.has_bucket
    elif ch == ' ':
        dbg.paused = not dbg.paused
    elif ch == '0':
        sim.__init__()
        dbg.history.clear()
    elif ch == '1':
        sim.has_ball = True; sim.has_bucket = False
        sim.ball_x = 100; sim.ball_y = 240; sim.ball_r = 20
    elif ch == '2':
        sim.has_ball = True; sim.has_bucket = False
        sim.ball_x = 320; sim.ball_y = 240; sim.ball_r = 50
    elif ch == '3':
        sim.has_ball = True; sim.has_bucket = False
        sim.ball_x = 320; sim.ball_y = 320; sim.ball_r = 80
    elif ch == '4':
        sim.has_ball = True; sim.has_bucket = False
        sim.ball_x = 275; sim.ball_y = 380; sim.ball_r = 90
    elif ch == '5':
        sim.has_ball = False; sim.has_bucket = True
        sim.bucket_w = 620; sim.bucket_h = 400
        sim.bucket_x = 10; sim.bucket_y = 40
    elif ch in ('\x03', '\x1b'):  # ctrl+c, esc
        dbg.running = False


# ── Main ──

def main():
    sim = SimWorld()
    global fsm
    fsm = HunterStateMachine(RobotGeometry())
    dbg = DebugState()
    renderer = TerminalRenderer()

    # Start keyboard thread
    kbd = threading.Thread(target=key_input, args=(sim, dbg), daemon=True)
    kbd.start()

    hide_cursor()
    last_t = time.time()

    try:
        while dbg.running:
            if dbg.paused:
                # Still render once then wait
                target = sim.get_target()
                out = fsm.update(target)
                renderer.render(sim, out, dbg)
                time.sleep(0.05)
                continue

            t0 = time.time()
            dt = min(0.1, t0 - last_t)
            last_t = t0

            target = sim.get_target()
            out = fsm.update(target)

            dbg.history.append(out.state)
            if len(dbg.history) > 60:
                dbg.history.pop(0)

            renderer.render(sim, out, dbg)

            elapsed = time.time() - t0
            dbg.fps = dbg.fps * 0.9 + (1.0 / elapsed if elapsed > 0 else 0) * 0.1

            # Cap at ~30fps
            sleep_t = 0.033 - elapsed
            if sleep_t > 0:
                time.sleep(sleep_t)

    except KeyboardInterrupt:
        pass
    finally:
        show_cursor()
        clear_screen()
        print(f'Final state: {fsm.state}')
        print(f'History: {" → ".join(set(dbg.history[-10:]))}')


if __name__ == '__main__':
    main()
