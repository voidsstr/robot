"""Claude-piloted autonomous navigation for the robot.

Two pieces:

  VisionNavigator — one Claude API call: given the next target-waypoint
                    photo + the current camera frame + a lidar sector
                    summary + recent action history, return a structured
                    next action.

  MissionRunner   — the outer loop that drives the robot toward each
                    waypoint of a saved route, using VisionNavigator for
                    decisions. Holds a hard lidar safety override (front
                    sector watchdog) that vetoes any forward action when
                    obstacles are too close.

Cost: each Claude call is roughly $0.03-0.05 with two 768px images +
~200 tokens out. The runner caps each mission at MAX_CALLS_DEFAULT.
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Literal, Callable

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from routes import (
    Route, Waypoint, lidar_sector_summary, front_min_m,
)

MODEL = 'claude-opus-4-7'
MAX_IMAGE_EDGE = 768          # send-down for cost; bigger doesn't help short missions
MAX_CALLS_DEFAULT = 100
MIN_CALL_INTERVAL_S = 1.0
LIDAR_FRONT_SAFETY_M = 0.5    # absolute veto: no forward if anything closer than this
WAYPOINT_ADVANCE_HOLD_S = 0.6  # tiny dwell after advancing so we don't re-fire the same frame


SYSTEM_PROMPT = """\
You are piloting a tank-tread lawn robot. You will receive a sequence of
images and lidar readings; your job is to navigate from the robot's
current position to match the TARGET waypoint photo, then continue to
the next waypoint, until the mission is complete.

Inputs each step:
  TARGET image  — what the camera should approximately see when the
                  robot has arrived at the current waypoint.
  CURRENT image — what the camera sees right now.
  LIDAR JSON   — nearest-obstacle distance in metres for each 30° sector
                  around the robot (12 sectors total; 'fwd' is straight
                  ahead).

Pick ONE action per step:
  forward  — drive forward (both tracks)
  backward — drive backward
  left     — rotate left in place
  right    — rotate right in place
  stop     — halt and wait (use to reassess)
  capture  — you have arrived at the FINAL destination; the system will
             take an official high-res photo
  done     — the entire mission is complete

duration_s ∈ [1, 5] — how long to execute the action. Use 1-2s for
small adjustments, 3-5s for sustained movement.

status ∈ {navigating, reached_waypoint, at_destination, stuck}
  navigating       — still working toward this waypoint
  reached_waypoint — current view matches the target well enough; advance
  at_destination   — this is the final waypoint; next step should be capture
  stuck            — cannot make progress (boxed in, lost, target not
                     achievable from here)

Hard safety rules:
- If LIDAR 'fwd' is less than 0.5 m, NEVER choose 'forward'. Turn or back up.
- If multiple sectors are < 0.5 m and you can't move, return status=stuck.
- Prefer 'stop' over a risky action when uncertain.

Always include a brief `reasoning` (~1 sentence) describing what you saw
and why this action follows. Be specific — "I see a path of grass to the
right" not "moving right"."""


# --------------------------------------------------------------------------- #
# Image helpers
# --------------------------------------------------------------------------- #
def _b64_resized_jpeg(jpeg_bytes, max_edge=MAX_IMAGE_EDGE):
    """Re-encode a JPEG down to max_edge on the long side. Cheaper inputs."""
    from PIL import Image  # type: ignore
    img = Image.open(io.BytesIO(jpeg_bytes))
    w, h = img.size
    if max(w, h) > max_edge:
        scale = max_edge / float(max(w, h))
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=85)
    return base64.standard_b64encode(buf.getvalue()).decode('ascii')


# --------------------------------------------------------------------------- #
# Vision navigator
# --------------------------------------------------------------------------- #
@dataclass
class NavDecision:
    action: str           # forward|backward|left|right|stop|capture|done
    duration_s: int
    status: str           # navigating|reached_waypoint|at_destination|stuck
    reasoning: str
    confidence: float
    raw: dict = field(default_factory=dict)


class VisionNavigator:
    """Wraps one Claude call per step."""

    def __init__(self, model: str = MODEL):
        self._model = model

    def decide(self,
               target_jpeg: bytes,
               current_jpeg: bytes,
               lidar_summary: dict,
               waypoint_idx: int,
               waypoint_total: int,
               waypoint_label: str,
               last_actions: list,
               steps_taken: int,
               calls_remaining: int) -> NavDecision:
        """Return the next action. Raises on transport / parse failure."""
        import anthropic
        from pydantic import BaseModel, Field

        class NavAction(BaseModel):
            action: Literal['forward', 'backward', 'left', 'right',
                            'stop', 'capture', 'done']
            duration_s: int = Field(ge=1, le=5,
                description='How long (seconds) to execute the action.')
            status: Literal['navigating', 'reached_waypoint',
                            'at_destination', 'stuck'] = Field(
                description='Mission-stage signal for the runner.')
            reasoning: str = Field(description='One-sentence justification.')
            confidence: float = Field(ge=0.0, le=1.0,
                description='How confident you are in this decision.')

        target_b64 = _b64_resized_jpeg(target_jpeg)
        current_b64 = _b64_resized_jpeg(current_jpeg)
        lidar_text = json.dumps(lidar_summary, separators=(',', ':'))

        history_lines = []
        for h in last_actions[-5:]:
            history_lines.append(
                f"  - {h.get('action','?')} for {h.get('duration_s','?')}s "
                f"(status={h.get('status','?')}, reasoning={h.get('reasoning','')[:80]})"
            )
        history_text = '\n'.join(history_lines) or '  (none yet)'

        user_text = (
            f"Waypoint {waypoint_idx + 1} of {waypoint_total}: \"{waypoint_label}\".\n"
            f"Steps taken so far: {steps_taken}. Calls remaining: {calls_remaining}.\n"
            f"Recent actions:\n{history_text}\n\n"
            f"Lidar (m, 30° sectors): {lidar_text}\n"
            "Now pick the next action."
        )

        client = anthropic.Anthropic()
        response = client.messages.parse(
            model=self._model,
            max_tokens=512,
            system=[{
                'type': 'text',
                'text': SYSTEM_PROMPT,
                'cache_control': {'type': 'ephemeral'},
            }],
            messages=[{
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': 'TARGET waypoint image:'},
                    {'type': 'image', 'source': {
                        'type': 'base64', 'media_type': 'image/jpeg', 'data': target_b64,
                    }},
                    {'type': 'text', 'text': 'CURRENT camera frame:'},
                    {'type': 'image', 'source': {
                        'type': 'base64', 'media_type': 'image/jpeg', 'data': current_b64,
                    }},
                    {'type': 'text', 'text': user_text},
                ],
            }],
            output_format=NavAction,
        )

        if response.parsed_output is None:
            text = next((b.text for b in response.content
                         if getattr(b, 'type', None) == 'text'), '')
            raise RuntimeError(f'Navigator returned no structured action '
                               f'(stop_reason={response.stop_reason}). Text: {text!r}')

        out = response.parsed_output.model_dump()
        return NavDecision(
            action=out['action'],
            duration_s=int(out['duration_s']),
            status=out['status'],
            reasoning=out['reasoning'],
            confidence=float(out.get('confidence', 0.0)),
            raw=out,
        )


# --------------------------------------------------------------------------- #
# Action execution
# --------------------------------------------------------------------------- #
def execute_action(client, action: str, duration_s: int):
    """Translate a navigator action into a wifi-server command sequence.

    The wifi-server protocol is step-based — each UP/DOWN/LEFT/RIGHT
    increments motor levels by ±3. We send a small burst (3 increments)
    to set a slow speed, hold for duration_s, then STOP.
    """
    if action in ('stop', 'capture', 'done'):
        client.send_cmd('STOP')
        return
    cmd_map = {'forward': 'UP', 'backward': 'DOWN', 'left': 'LEFT', 'right': 'RIGHT'}
    cmd = cmd_map.get(action)
    if cmd is None:
        client.send_cmd('STOP')
        return
    # Ramp up
    for _ in range(3):
        client.send_cmd(cmd)
        time.sleep(0.05)
    # Hold (subtract ramp time)
    hold = max(0.3, float(duration_s) - 0.15)
    time.sleep(hold)
    client.send_cmd('STOP')


# --------------------------------------------------------------------------- #
# Mission runner
# --------------------------------------------------------------------------- #
@dataclass
class MissionStatus:
    state: str = 'idle'          # idle|running|completed|aborted|failed
    leg: str = ''                # outbound|destination|return
    current_wp: int = 0
    total_wp: int = 0
    steps: int = 0
    calls: int = 0
    calls_max: int = MAX_CALLS_DEFAULT
    last_action: str = ''
    last_reasoning: str = ''
    error: str = ''
    started_at: float = 0.0


class MissionRunner:
    """Runs a Claude-piloted mission against a stored Route.

    Each step:
      1. Grab the current camera frame + lidar scan.
      2. If lidar front < 0.5 m, force STOP and warn the navigator next call.
      3. Call VisionNavigator with target + current + lidar + history.
      4. If response says forward but lidar.fwd < 0.5 m → override to stop.
      5. Execute the action via wifi-server.
      6. If status == reached_waypoint → advance.
      7. If status == at_destination → take official lawn photo via lawn_camera.
      8. After destination: reverse the waypoint list and run the return leg.

    All steps are logged to ~/robot-routes/<route>/missions/<id>/log.jsonl.
    """

    def __init__(self,
                 route: Route,
                 client,                                # CommandClient
                 frame_getter: Callable[[], bytes],    # returns latest jpeg
                 lidar_getter: Callable[[], list],     # returns latest (ang,dist_m) points
                 navigator: Optional[VisionNavigator] = None,
                 max_calls: int = MAX_CALLS_DEFAULT,
                 min_call_interval_s: float = MIN_CALL_INTERVAL_S,
                 on_status: Callable[['MissionStatus'], None] = None,
                 on_log: Callable[[str, str], None] = None,
                 lawn_photo_cb: Optional[Callable[[], Optional[bytes]]] = None):
        self._route = route
        self._client = client
        self._frame_getter = frame_getter
        self._lidar_getter = lidar_getter
        self._navigator = navigator or VisionNavigator()
        self._max_calls = max_calls
        self._min_call_interval_s = min_call_interval_s
        self._on_status = on_status or (lambda _s: None)
        self._on_log = on_log or (lambda _tag, _msg: None)
        self._lawn_photo_cb = lawn_photo_cb
        self._abort = threading.Event()
        self._thread = None
        self._mission_dir = ''
        self._log_f = None

        self.status = MissionStatus()
        self.status.total_wp = len(route.waypoints)
        self.status.calls_max = max_calls
        self._history: list[dict] = []

    # ----- public ----------------------------------------------------------

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        if not self._route.waypoints:
            self.status.state = 'failed'
            self.status.error = 'route has no waypoints'
            self._on_status(self.status)
            return
        self._abort.clear()
        self.status = MissionStatus(
            state='running', leg='outbound',
            total_wp=len(self._route.waypoints),
            calls_max=self._max_calls,
            started_at=time.time(),
        )
        self._history.clear()
        self._open_session_log()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name='mission-runner')
        self._thread.start()
        self._on_status(self.status)

    def abort(self):
        self._abort.set()
        try:
            self._client.send_cmd('STOP')
        except Exception:
            pass
        self._on_log('INFO', 'mission abort requested')

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    # ----- run -------------------------------------------------------------

    def _run(self):
        try:
            self._do_leg(forward=True, leg_name='outbound')
            if self._abort.is_set(): return self._finalise('aborted')
            if self._lawn_photo_cb is not None:
                self._on_log('INFO', 'destination reached — taking lawn photo')
                try:
                    self._lawn_photo_cb()
                except Exception as e:
                    self._on_log('ERROR', f'destination photo failed: {e}')
            if self._abort.is_set(): return self._finalise('aborted')
            self.status.leg = 'return'
            self._do_leg(forward=False, leg_name='return')
            if self._abort.is_set(): return self._finalise('aborted')
            self._finalise('completed')
        except Exception as e:
            self.status.error = str(e)
            self._on_log('ERROR', f'mission crashed: {e}')
            self._finalise('failed')

    def _do_leg(self, forward: bool, leg_name: str):
        wp_list = list(self._route.waypoints)
        if not forward:
            wp_list = list(reversed(wp_list))
        for i, wp in enumerate(wp_list):
            if self._abort.is_set(): return
            self.status.current_wp = i if forward else (len(wp_list) - 1 - i)
            self.status.leg = leg_name
            self._on_status(self.status)
            self._navigate_to(wp, is_final=(i == len(wp_list) - 1) and forward)
            time.sleep(WAYPOINT_ADVANCE_HOLD_S)

    def _navigate_to(self, wp: Waypoint, is_final: bool):
        """Loop until navigator says reached_waypoint / at_destination /
        stuck, or the budget runs out."""
        target_path = os.path.join(self._route.root, wp.image_file)
        try:
            with open(target_path, 'rb') as f:
                target_jpeg = f.read()
        except Exception as e:
            self._on_log('ERROR', f'missing waypoint image {wp.image_file}: {e}')
            return

        last_call_ts = 0.0
        while not self._abort.is_set():
            # Budget checks.
            if self.status.calls >= self._max_calls:
                self.status.error = 'call budget exhausted'
                self._on_log('ERROR', 'mission call budget exhausted')
                self.abort()
                return

            # Rate limit.
            now = time.monotonic()
            wait = self._min_call_interval_s - (now - last_call_ts)
            if wait > 0:
                if self._abort.wait(wait):
                    return

            current_jpeg = self._frame_getter()
            lidar_points = self._lidar_getter() or []
            lidar_sum = lidar_sector_summary(lidar_points)

            # Hard safety override.
            if (front_min_m(lidar_sum) is not None
                    and front_min_m(lidar_sum) < LIDAR_FRONT_SAFETY_M):
                self._client.send_cmd('STOP')
                self._on_log('WARN', f'lidar front {front_min_m(lidar_sum)}m — '
                                     'forced STOP, asking navigator')

            try:
                decision = self._navigator.decide(
                    target_jpeg=target_jpeg,
                    current_jpeg=current_jpeg or b'',
                    lidar_summary=lidar_sum,
                    waypoint_idx=wp.idx,
                    waypoint_total=len(self._route.waypoints),
                    waypoint_label=wp.label,
                    last_actions=self._history,
                    steps_taken=self.status.steps,
                    calls_remaining=self._max_calls - self.status.calls,
                )
            except Exception as e:
                self._on_log('ERROR', f'navigator call failed: {e}')
                self._client.send_cmd('STOP')
                # Back off a beat; let the user abort if it keeps failing.
                if self._abort.wait(2.0): return
                continue
            last_call_ts = time.monotonic()
            self.status.calls += 1

            # Lidar override: don't forward into something close.
            if (decision.action == 'forward'
                    and front_min_m(lidar_sum) is not None
                    and front_min_m(lidar_sum) < LIDAR_FRONT_SAFETY_M):
                self._on_log('WARN', 'override: navigator said forward but '
                                     'lidar < 0.5m → STOP')
                decision = NavDecision(
                    action='stop', duration_s=1, status='navigating',
                    reasoning='lidar safety override (' + decision.reasoning + ')',
                    confidence=decision.confidence, raw=decision.raw,
                )

            self.status.last_action = f'{decision.action} {decision.duration_s}s'
            self.status.last_reasoning = decision.reasoning
            self._on_status(self.status)
            self._log_step(wp, current_jpeg, lidar_sum, decision)
            self._history.append({
                'action': decision.action, 'duration_s': decision.duration_s,
                'status': decision.status, 'reasoning': decision.reasoning,
            })
            self.status.steps += 1

            # Execute (unless we're done with this waypoint).
            if decision.status in ('reached_waypoint',):
                self._client.send_cmd('STOP')
                self._on_log('INFO', f'reached waypoint {wp.idx}: {decision.reasoning}')
                return
            if decision.status == 'at_destination' and is_final:
                self._client.send_cmd('STOP')
                self._on_log('INFO', 'at destination — finishing waypoint')
                return
            if decision.status == 'stuck':
                self._on_log('ERROR', f'navigator stuck: {decision.reasoning}')
                self.status.error = 'navigator reported stuck'
                self.abort()
                return

            execute_action(self._client, decision.action, decision.duration_s)

    # ----- logging ---------------------------------------------------------

    def _open_session_log(self):
        rid = time.strftime('%Y%m%d-%H%M%S')
        self._mission_dir = os.path.join(self._route.root, 'missions', rid)
        os.makedirs(self._mission_dir, exist_ok=True)
        self._log_f = open(os.path.join(self._mission_dir, 'log.jsonl'), 'w')
        self._on_log('INFO', f'mission log: {self._mission_dir}')

    def _log_step(self, wp, current_jpeg, lidar_sum, decision):
        if self._log_f is None: return
        step = self.status.steps
        img_name = f'step-{step:04d}.jpg'
        if current_jpeg:
            try:
                with open(os.path.join(self._mission_dir, img_name), 'wb') as f:
                    f.write(current_jpeg)
            except Exception:
                img_name = ''
        try:
            self._log_f.write(json.dumps({
                'step': step,
                'wall_t': time.time(),
                'waypoint_idx': wp.idx,
                'waypoint_label': wp.label,
                'image': img_name,
                'lidar': lidar_sum,
                'decision': decision.raw,
            }) + '\n')
            self._log_f.flush()
        except Exception:
            pass

    def _finalise(self, state: str):
        self.status.state = state
        self._on_status(self.status)
        try:
            if self._log_f is not None:
                self._log_f.close()
                self._log_f = None
        except Exception:
            pass
        self._on_log('INFO', f'mission {state}')
