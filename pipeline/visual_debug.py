"""
visual_debug.py — Real-time visual debugger for the hunter state machine.

Shows a simulated camera frame with a draggable tennis ball, detection
bounding box, 9-grid overlay, state machine status, motor speed indicators,
and state history.

Controls:
  - Drag the ball with mouse to test different positions/scenarios
  - Press 'r' to toggle red bucket mode
  - Press 'space' to pause/resume
  - Press 's' to step one frame (when paused)
  - Press '0' to reset
  - Press '1'-'5' for preset scenarios
  - Scroll wheel to resize ball

Usage:
  python pipeline/visual_debug.py
"""

import sys
import os
import time
from dataclasses import dataclass

import numpy as np
import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, FancyBboxPatch, Circle, Polygon
from matplotlib.lines import Line2D

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.state_machine import (
    HunterStateMachine, TargetInfo, State, RobotGeometry,
)


# ═══════════════════════════════════════════════════════════════════════
# Visual config
# ═══════════════════════════════════════════════════════════════════════

FRAME_W, FRAME_H = 640, 480
COLORS = {
    State.CHASE_TENNIS:    '#f97316',  # orange
    State.POSITION_TENNIS: '#3b82f6',  # blue
    State.GRAB_TENNIS:     '#22c55e',  # green
    State.CHASE_BUCKET:    '#8b5cf6',  # purple
    State.RELEASE_TENNIS:  '#ef4444',  # red
    'grid': '#374151',
    'grab_zone': 'rgba(34,197,94,0.25)',
    'box': '#22c55e',
    'ball': '#facc15',
    'bucket': '#ef4444',
    'bg': '#0f172a',
    'panel': '#1e293b',
    'text': '#e2e8f0',
    'text_dim': '#64748b',
}


# ═══════════════════════════════════════════════════════════════════════
# Simulated ball + bucket
# ═══════════════════════════════════════════════════════════════════════

class SimWorld:
    """Manages the simulated environment: ball position, bucket, etc."""

    def __init__(self):
        self.ball_x = 320.0   # center of ball (pixels)
        self.ball_y = 240.0
        self.ball_r = 30.0    # radius in pixels
        self.ball_vx = 0.0
        self.ball_vy = 0.0
        self.has_ball = True

        self.bucket_x = 0.0
        self.bucket_y = 0.0
        self.bucket_w = 300.0
        self.bucket_h = 250.0
        self.has_bucket = False

        self._dragging = False

    def get_target(self) -> TargetInfo:
        if self.has_bucket:
            return TargetInfo(
                has_target=True,
                x=self.bucket_x, y=self.bucket_y,
                w=self.bucket_w, h=self.bucket_h,
                confidence=0.80,
            )
        if not self.has_ball:
            return TargetInfo()
        # Ball → bounding box (approximate)
        w = self.ball_r * 2.5
        h = self.ball_r * 2.5
        return TargetInfo(
            has_target=True,
            x=self.ball_x - w / 2, y=self.ball_y - h / 2,
            w=w, h=h,
            confidence=0.90 + np.random.uniform(-0.03, 0.03),
        )

    def update_physics(self, dt: float):
        """Simple physics: ball drifts slowly."""
        if self.has_ball and not self._dragging:
            # Gentle random drift
            self.ball_vx += np.random.uniform(-5, 5) * dt
            self.ball_vy += np.random.uniform(-5, 5) * dt
            self.ball_vx *= 0.95
            self.ball_vy *= 0.95
            self.ball_x += self.ball_vx * dt
            self.ball_y += self.ball_vy * dt
            # Bounce off edges
            margin = self.ball_r + 10
            if self.ball_x < margin:
                self.ball_x = margin; self.ball_vx *= -0.8
            if self.ball_x > FRAME_W - margin:
                self.ball_x = FRAME_W - margin; self.ball_vx *= -0.8
            if self.ball_y < margin:
                self.ball_y = margin; self.ball_vy *= -0.8
            if self.ball_y > FRAME_H - margin:
                self.ball_y = FRAME_H - margin; self.ball_vy *= -0.8


# ═══════════════════════════════════════════════════════════════════════
# Main visualizer
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class DebugState:
    """Tracks running state for the debugger."""
    running: bool = True
    paused: bool = False
    step: bool = False
    fps: float = 0.0
    history: list = None

    def __post_init__(self):
        self.history = []


def main():
    world = SimWorld()
    fsm = HunterStateMachine(RobotGeometry())
    dbg = DebugState()

    # ── Setup figure ──
    plt.style.use('dark_background')
    fig = plt.figure(figsize=(14, 7), facecolor='#020617')
    fig.canvas.manager.set_window_title('SG2002 Hunter — Visual Debug')

    # Grid layout
    gs = fig.add_gridspec(1, 2, width_ratios=[1.4, 1], wspace=0.04)

    ax_frame = fig.add_subplot(gs[0])
    ax_frame.set_xlim(0, FRAME_W)
    ax_frame.set_ylim(FRAME_H, 0)  # flip Y for image coords
    ax_frame.set_facecolor('#0a0a0a')
    ax_frame.set_xticks([])
    ax_frame.set_yticks([])
    ax_frame.set_aspect('equal')

    ax_panel = fig.add_subplot(gs[1])
    ax_panel.set_xlim(0, 10)
    ax_panel.set_ylim(0, 10)
    ax_panel.set_xticks([])
    ax_panel.set_yticks([])
    ax_panel.set_facecolor('#0f172a')

    fig.subplots_adjust(left=0.02, right=0.98, top=0.95, bottom=0.05, wspace=0.04)

    # ── Draw static frame elements ──

    # 9-grid lines
    g = fsm.geom
    for x_frac in [g.left_boundary, g.right_boundary]:
        x = x_frac * FRAME_W
        ax_frame.axvline(x=x, color=COLORS['grid'], linewidth=0.5, linestyle='--', alpha=0.4)
    for y_frac in [g.top_boundary, g.bottom_boundary]:
        y = y_frac * FRAME_H
        ax_frame.axhline(y=y, color=COLORS['grid'], linewidth=0.5, linestyle='--', alpha=0.4)

    # Grab zone (bottom center)
    grab_zone = Rectangle(
        (g.x_left_grab, FRAME_H * g.bottom_boundary),
        g.x_right_grab - g.x_left_grab,
        FRAME_H - FRAME_H * g.bottom_boundary,
        facecolor='#22c55e15', edgecolor='#22c55e', linewidth=1, linestyle='--',
        label='Grab Zone',
    )
    ax_frame.add_patch(grab_zone)
    ax_frame.text(g.x_left_grab + 2, FRAME_H - 10, 'GRAB ZONE',
                  color='#22c55e88', fontsize=7, va='top', ha='left')

    # ── Dynamic elements (created, updated each frame) ──
    ball_circle = Circle((200, 200), 30, facecolor=COLORS['ball'], edgecolor='#eab308',
                         linewidth=2, alpha=0.8, zorder=10)
    ax_frame.add_patch(ball_circle)

    box_rect = Rectangle((0, 0), 0, 0, fill=False, edgecolor=COLORS['box'],
                         linewidth=2, zorder=9)
    ax_frame.add_patch(box_rect)

    box_label = ax_frame.text(0, 0, '', color='#22c55e', fontsize=8, va='bottom', ha='left', zorder=11)

    # Bucket indicator
    bucket_rect = Rectangle((0, 0), 0, 0, fill=False, edgecolor=COLORS['bucket'],
                            linewidth=2, linestyle='--', zorder=8)
    ax_frame.add_patch(bucket_rect)

    # ── Panel elements ──
    # Title
    ax_panel.text(5, 9.5, 'ROBOT STATUS', color=COLORS['text'],
                  fontsize=14, fontweight='bold', ha='center', va='center',
                  fontfamily='monospace')

    # State indicator (large)
    state_text = ax_panel.text(5, 8.3, '', color='#fff', fontsize=22, fontweight='bold',
                                ha='center', va='center', fontfamily='monospace')

    # State transition arrow
    state_arrow = ax_panel.text(5, 7.5, '', color=COLORS['text_dim'],
                                 fontsize=10, ha='center', va='center', fontfamily='monospace')

    # Info lines
    info_lines = []
    for i in range(8):
        t = ax_panel.text(0.5, 6.8 - i * 0.6, '', color=COLORS['text_dim'],
                          fontsize=9, fontfamily='monospace', va='top')
        info_lines.append(t)

    # Motor speed bars
    motor_bg_left = Rectangle((0.5, 1.0), 4, 0.4, facecolor='#1e293b', edgecolor='#334155', linewidth=1)
    motor_bg_right = Rectangle((5.5, 1.0), 4, 0.4, facecolor='#1e293b', edgecolor='#334155', linewidth=1)
    ax_panel.add_patch(motor_bg_left)
    ax_panel.add_patch(motor_bg_right)

    motor_fill_left = Rectangle((0.5, 1.0), 0, 0.4, facecolor='#3b82f6', edgecolor='none')
    motor_fill_right = Rectangle((5.5, 1.0), 0, 0.4, facecolor='#3b82f6', edgecolor='none')
    ax_panel.add_patch(motor_fill_left)
    ax_panel.add_patch(motor_fill_right)

    motor_label_left = ax_panel.text(2.5, 1.65, 'L:  0', color='#94a3b8',
                                     fontsize=10, ha='center', fontfamily='monospace', fontweight='bold')
    motor_label_right = ax_panel.text(7.5, 1.65, 'R:  0', color='#94a3b8',
                                      fontsize=10, ha='center', fontfamily='monospace', fontweight='bold')

    # State history timeline
    ax_panel.text(0.5, 0.3, 'STATE HISTORY', color=COLORS['text_dim'], fontsize=8, fontfamily='monospace')
    history_patches = []

    # FPS
    fps_text = ax_panel.text(9.5, 9.7, '0 FPS', color=COLORS['text_dim'],
                             fontsize=8, ha='right', fontfamily='monospace')

    # Help
    ax_panel.text(5, 0.1, 'drag ball | 1-5 presets | r=bucket | space=pause | s=step',
                  color='#334155', fontsize=7, ha='center', fontfamily='monospace')

    # ── Event handlers ──

    def on_mouse_press(event):
        if event.inaxes != ax_frame:
            return
        dx = event.xdata - world.ball_x
        dy = event.ydata - world.ball_y
        if dx*dx + dy*dy < (world.ball_r * 2)**2:
            world._dragging = True

    def on_mouse_release(event):
        world._dragging = False
        world.ball_vx = 0
        world.ball_vy = 0

    def on_mouse_move(event):
        if world._dragging and event.inaxes == ax_frame and event.xdata and event.ydata:
            world.ball_x = float(event.xdata)
            world.ball_y = float(event.ydata)

    def on_scroll(event):
        if event.inaxes == ax_frame:
            world.ball_r = max(10, min(200, world.ball_r + event.step * 5))

    def on_key(event):
        if event.key == ' ':
            dbg.paused = not dbg.paused
        elif event.key == 's':
            dbg.step = True
        elif event.key == 'r':
            world.has_bucket = not world.has_bucket
            if world.has_bucket:
                world.has_ball = False
            else:
                world.has_ball = True
        elif event.key == '0':
            world.__init__()
            fsm.reset()
        elif event.key == '1':  # Far target on left
            world.has_ball = True; world.has_bucket = False
            world.ball_x = 100; world.ball_y = 240; world.ball_r = 20
            world.ball_vx = 0; world.ball_vy = 0
        elif event.key == '2':  # Center approaching
            world.has_ball = True; world.has_bucket = False
            world.ball_x = 320; world.ball_y = 240; world.ball_r = 50
            world.ball_vx = 0; world.ball_vy = 0
        elif event.key == '3':  # In position zone
            world.has_ball = True; world.has_bucket = False
            world.ball_x = 320; world.ball_y = 320; world.ball_r = 80
            world.ball_vx = 0; world.ball_vy = 0
        elif event.key == '4':  # In grab zone
            world.has_ball = True; world.has_bucket = False
            world.ball_x = 275; world.ball_y = 380; world.ball_r = 90
            world.ball_vx = 0; world.ball_vy = 0
        elif event.key == '5':  # Bucket filling frame
            world.has_ball = False; world.has_bucket = True
            world.bucket_w = 620; world.bucket_h = 400
            world.bucket_x = 10; world.bucket_y = 40

    fig.canvas.mpl_connect('button_press_event', on_mouse_press)
    fig.canvas.mpl_connect('button_release_event', on_mouse_release)
    fig.canvas.mpl_connect('motion_notify_event', on_mouse_move)
    fig.canvas.mpl_connect('scroll_event', on_scroll)
    fig.canvas.mpl_connect('key_press_event', on_key)

    # ── Main loop ──
    last_frame_time = time.time()
    state_history = []

    while dbg.running:
        # Check if we should process this frame
        if dbg.paused and not dbg.step:
            fig.canvas.flush_events()
            plt.pause(0.05)
            continue
        dbg.step = False

        t_start = time.time()

        # Physics
        dt = min(0.1, t_start - last_frame_time)
        last_frame_time = t_start
        world.update_physics(dt)

        # State machine
        target = world.get_target()
        out = fsm.update(target)

        # ── Update visuals ──

        # Ball
        if world.has_ball:
            ball_circle.set(center=(world.ball_x, world.ball_y), radius=world.ball_r)
            ball_circle.set_visible(True)
        else:
            ball_circle.set_visible(False)

        # Detection box
        if target.has_target:
            box_rect.set(xy=(target.x, target.y), width=target.w, height=target.h)
            box_rect.set_visible(True)
            box_label.set(position=(target.x, target.y - 5),
                          text=f'{target.w:.0f}x{target.h:.0f}')
            box_label.set_visible(True)
        else:
            box_rect.set_visible(False)
            box_label.set_visible(False)

        # Bucket
        if world.has_bucket:
            bucket_rect.set(xy=(world.bucket_x, world.bucket_y),
                            width=world.bucket_w, height=world.bucket_h)
            bucket_rect.set_visible(True)
        else:
            bucket_rect.set_visible(False)

        # State text
        state_color = COLORS.get(out.state, '#fff')
        state_text.set(text=out.state.upper().replace('_', ' '), color=state_color)

        # State arrow
        if len(dbg.history) >= 2:
            prev = dbg.history[-2]
            if prev != out.state:
                state_arrow.set(text=f'{prev} → {out.state}')
            else:
                state_arrow.set(text='')
        else:
            state_arrow.set(text='')

        # Info lines
        infos = [
            f'box:   x={out.box_x:.0f}  w={out.box_w:.0f}  h={out.box_h:.0f}',
            f'center: x={target.cx:.0f}  y={target.cy:.0f}' if target.has_target else 'center: —',
            f'conf:  {target.confidence:.3f}' if target.has_target else 'conf:  —',
            '',
            f'grab confirm: {out.grab_confirm}/{fsm.geom.grab_confirm_frames}',
            f'elapsed: {fsm.elapsed_in_state:.1f}s',
            '',
            f'motor: L={out.car_left:+.0f}  R={out.car_right:+.0f}',
        ]
        for i, info in enumerate(infos):
            info_lines[i].set_text(info)

        # Motor speed bars
        max_w = 4.0
        left_w = max_w * abs(out.car_left) / 100.0
        right_w = max_w * abs(out.car_right) / 100.0
        left_color = '#22c55e' if out.car_left >= 0 else '#ef4444'
        right_color = '#22c55e' if out.car_right >= 0 else '#ef4444'

        motor_fill_left.set(width=left_w, facecolor=left_color)
        motor_fill_right.set(width=right_w, facecolor=right_color)
        motor_label_left.set(text=f'L: {out.car_left:+.0f}')
        motor_label_right.set(text=f'R: {out.car_right:+.0f}')

        # History
        state_history.append(out.state)
        if len(state_history) > 50:
            state_history.pop(0)
        dbg.history = state_history

        # Update history display
        for p in history_patches:
            p.remove()
        history_patches.clear()
        bar_w = 9.0 / 50
        for i, s in enumerate(state_history):
            color = COLORS.get(s, '#fff')
            rect = Rectangle((0.5 + i * bar_w, 0.35), bar_w, 0.35,
                             facecolor=color, edgecolor='none', alpha=0.7)
            ax_panel.add_patch(rect)
            history_patches.append(rect)

        # FPS
        elapsed = time.time() - t_start
        instant_fps = 1.0 / elapsed if elapsed > 0 else 0
        dbg.fps = dbg.fps * 0.9 + instant_fps * 0.1 if dbg.fps > 0 else instant_fps
        fps_text.set(text=f'{dbg.fps:.0f} FPS')

        # Render
        fig.canvas.draw_idle()
        fig.canvas.flush_events()

    plt.close()


if __name__ == '__main__':
    main()
