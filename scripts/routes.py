"""Route + waypoint storage for the training / mission system.

Layout on disk (default root: ~/robot-routes):

    <root>/<route_name>/
        route.json                  — manifest (waypoint list, metadata)
        waypoint-0000.jpg           — reference photo at this waypoint
        waypoint-0000.json          — lidar summary + optional GPS + label
        ...
        streams/<recording_id>/     — raw continuous-capture stream
            frame-000000.jpg
            frame-000000.json
            ...
        missions/<run_id>/          — one folder per Claude-piloted run
            log.jsonl
            step-000.jpg            — current frame seen at each step
            step-000.json           — lidar + Claude action + executed cmd

The format stays human-greppable so we can diff routes by hand and so
a stream/mission folder can be replayed offline without the live robot.
"""

from __future__ import annotations

import json
import math
import os
import time
import shutil
from dataclasses import dataclass, field
from typing import Optional


DEFAULT_ROUTES_DIR = os.environ.get(
    'ROBOT_ROUTES_DIR',
    os.path.expanduser('~/robot-routes'),
)


# --------------------------------------------------------------------------- #
# Lidar sector summary
# --------------------------------------------------------------------------- #
# 12 sectors of 30° each, indexed clockwise starting at 0° = forward.
SECTOR_DEG = 30
SECTOR_COUNT = 360 // SECTOR_DEG
SECTOR_LABELS = [
    'fwd',        # 345-15
    'fwd_r',      # 15-45
    'right_fwd',  # 45-75
    'right',      # 75-105
    'right_back', # 105-135
    'back_r',     # 135-165
    'back',       # 165-195
    'back_l',     # 195-225
    'left_back',  # 225-255
    'left',       # 255-285
    'left_fwd',   # 285-315
    'fwd_l',      # 315-345
]


def lidar_sector_summary(points):
    """Compress a lidar scan into per-sector nearest-hit distances (metres).

    Input `points` is the same shape the LidarPanel uses: iterable of
    (angle_deg, dist_m). Output is a dict ready to be JSON-dumped into a
    prompt or saved to disk.
    """
    sectors = {label: None for label in SECTOR_LABELS}
    count = 0
    closest_overall = (None, None)  # (label, dist_m)
    for ang_raw, dist_m in points:
        if dist_m is None or dist_m <= 0:
            continue
        # Rotate so 0° = forward maps to sector 0 (which spans -15..+15).
        ang = (float(ang_raw) + 15.0) % 360.0
        idx = int(ang // SECTOR_DEG) % SECTOR_COUNT
        label = SECTOR_LABELS[idx]
        d = float(dist_m)
        prev = sectors[label]
        if prev is None or d < prev:
            sectors[label] = d
        if closest_overall[1] is None or d < closest_overall[1]:
            closest_overall = (label, d)
        count += 1
    return {
        'sectors_m': {k: (round(v, 2) if v is not None else None) for k, v in sectors.items()},
        'closest': {
            'sector': closest_overall[0],
            'm': (round(closest_overall[1], 2) if closest_overall[1] is not None else None),
        },
        'points': count,
    }


def front_min_m(sector_summary):
    """Convenience: nearest hit in the forward sector (or None)."""
    return sector_summary['sectors_m'].get('fwd')


# --------------------------------------------------------------------------- #
# Route + waypoint
# --------------------------------------------------------------------------- #
@dataclass
class Waypoint:
    idx: int
    label: str
    captured_at: float
    image_file: str          # relative to route dir
    lidar: dict              # sector summary
    gps: Optional[dict] = None  # {lat, lng, ...} if available


@dataclass
class Route:
    name: str
    root: str                # absolute path to ~/robot-routes/<name>
    waypoints: list[Waypoint] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    notes: str = ''

    def manifest_path(self):
        return os.path.join(self.root, 'route.json')

    def to_json(self):
        return {
            'name': self.name,
            'created_at': self.created_at,
            'notes': self.notes,
            'waypoints': [
                {
                    'idx': w.idx, 'label': w.label, 'captured_at': w.captured_at,
                    'image_file': w.image_file, 'lidar': w.lidar, 'gps': w.gps,
                }
                for w in self.waypoints
            ],
        }

    def save(self):
        os.makedirs(self.root, exist_ok=True)
        with open(self.manifest_path(), 'w') as f:
            json.dump(self.to_json(), f, indent=2)


def list_routes(routes_dir=DEFAULT_ROUTES_DIR):
    """Return list of route names that have a valid manifest."""
    if not os.path.isdir(routes_dir):
        return []
    out = []
    for name in sorted(os.listdir(routes_dir)):
        manifest = os.path.join(routes_dir, name, 'route.json')
        if os.path.isfile(manifest):
            out.append(name)
    return out


def load_route(name, routes_dir=DEFAULT_ROUTES_DIR) -> Route:
    root = os.path.join(routes_dir, name)
    manifest = os.path.join(root, 'route.json')
    if not os.path.isfile(manifest):
        raise FileNotFoundError(f'No route manifest at {manifest}')
    with open(manifest) as f:
        data = json.load(f)
    route = Route(
        name=data['name'],
        root=root,
        created_at=data.get('created_at', time.time()),
        notes=data.get('notes', ''),
    )
    route.waypoints = [
        Waypoint(
            idx=w['idx'], label=w.get('label', ''),
            captured_at=w.get('captured_at', 0.0),
            image_file=w['image_file'],
            lidar=w.get('lidar', {}),
            gps=w.get('gps'),
        )
        for w in data.get('waypoints', [])
    ]
    return route


def create_route(name, routes_dir=DEFAULT_ROUTES_DIR, overwrite=False) -> Route:
    """Create (or wipe + recreate, if overwrite) a route directory."""
    safe = _safe_route_name(name)
    root = os.path.join(routes_dir, safe)
    if os.path.exists(root) and overwrite:
        shutil.rmtree(root)
    os.makedirs(root, exist_ok=True)
    route = Route(name=safe, root=root)
    route.save()
    return route


def _safe_route_name(name):
    """Restrict to safe-for-filesystem characters."""
    out = ''
    for ch in name.strip():
        if ch.isalnum() or ch in ('-', '_'):
            out += ch
        elif ch.isspace():
            out += '-'
    return out or 'route'


def save_waypoint(route: Route, jpeg_bytes: bytes, lidar_points,
                  label: str = '', gps=None) -> Waypoint:
    """Append a waypoint to `route` and write its image + JSON sidecar."""
    idx = len(route.waypoints)
    image_file = f'waypoint-{idx:04d}.jpg'
    json_file  = f'waypoint-{idx:04d}.json'
    with open(os.path.join(route.root, image_file), 'wb') as f:
        f.write(jpeg_bytes)
    summary = lidar_sector_summary(lidar_points or [])
    wp = Waypoint(
        idx=idx,
        label=label or f'waypoint {idx}',
        captured_at=time.time(),
        image_file=image_file,
        lidar=summary,
        gps=gps,
    )
    with open(os.path.join(route.root, json_file), 'w') as f:
        json.dump({
            'idx': wp.idx, 'label': wp.label, 'captured_at': wp.captured_at,
            'image_file': wp.image_file, 'lidar': summary, 'gps': gps,
        }, f, indent=2)
    route.waypoints.append(wp)
    route.save()
    return wp


# --------------------------------------------------------------------------- #
# Continuous capture stream (training mode "Continuous")
# --------------------------------------------------------------------------- #
@dataclass
class StreamRecorder:
    """Buffer raw frames + lidar to disk; distill into waypoints at End Capture.

    Designed for cheap append on the GUI thread (a single file write per
    frame, no in-memory growth). The frames live under
    <route>/streams/<recording_id>/.
    """
    route: Route
    recording_id: str
    stream_dir: str
    frame_count: int = 0
    started_at: float = field(default_factory=time.time)

    @classmethod
    def begin(cls, route: Route) -> 'StreamRecorder':
        rid = time.strftime('%Y%m%d-%H%M%S')
        stream_dir = os.path.join(route.root, 'streams', rid)
        os.makedirs(stream_dir, exist_ok=True)
        return cls(route=route, recording_id=rid, stream_dir=stream_dir)

    def push(self, jpeg_bytes: bytes, lidar_points, gps=None):
        n = self.frame_count
        img = os.path.join(self.stream_dir, f'frame-{n:06d}.jpg')
        meta = os.path.join(self.stream_dir, f'frame-{n:06d}.json')
        with open(img, 'wb') as f:
            f.write(jpeg_bytes)
        with open(meta, 'w') as f:
            json.dump({
                'frame': n,
                'captured_at': time.time(),
                'lidar': lidar_sector_summary(lidar_points or []),
                'gps': gps,
            }, f)
        self.frame_count += 1

    def distill_waypoints(self, min_seconds: float = 3.0,
                          min_lidar_change_m: float = 0.5,
                          keep_first_and_last: bool = True) -> list[int]:
        """Walk the stream and pick a subset as waypoints.

        Triggers a new waypoint when EITHER:
          - `min_seconds` have passed since the last picked frame, OR
          - the lidar front-sector min has changed by `min_lidar_change_m`
            since the last picked frame (the robot has moved into a new
            environment).

        Returns the list of newly-appended waypoint indices.
        """
        picks = []
        frames = sorted(
            f for f in os.listdir(self.stream_dir)
            if f.endswith('.json') and f.startswith('frame-')
        )
        if not frames:
            return picks

        last_pick_t = 0.0
        last_pick_front = None

        for i, meta_name in enumerate(frames):
            with open(os.path.join(self.stream_dir, meta_name)) as f:
                meta = json.load(f)
            t = meta['captured_at']
            front = (meta.get('lidar') or {}).get('sectors_m', {}).get('fwd')

            first = (i == 0)
            last  = (i == len(frames) - 1)

            should_pick = False
            if keep_first_and_last and (first or last):
                should_pick = True
            elif t - last_pick_t >= min_seconds:
                should_pick = True
            elif (last_pick_front is not None and front is not None
                  and abs(front - last_pick_front) >= min_lidar_change_m):
                should_pick = True

            if should_pick:
                img_name = meta_name.replace('.json', '.jpg')
                with open(os.path.join(self.stream_dir, img_name), 'rb') as imf:
                    jpeg = imf.read()
                # Re-load lidar sector dict directly so we don't recompute.
                wp_label = f'auto-{meta["frame"]:06d}'
                wp = save_waypoint(self.route, jpeg, [], label=wp_label,
                                   gps=meta.get('gps'))
                # save_waypoint regenerated lidar from empty points; patch
                # the on-disk sidecar with the stream's real summary.
                sidecar = os.path.join(self.route.root, f'waypoint-{wp.idx:04d}.json')
                with open(sidecar) as f:
                    side = json.load(f)
                side['lidar'] = meta.get('lidar') or side['lidar']
                with open(sidecar, 'w') as f:
                    json.dump(side, f, indent=2)
                wp.lidar = side['lidar']
                self.route.save()
                picks.append(wp.idx)
                last_pick_t = t
                last_pick_front = front
        return picks
