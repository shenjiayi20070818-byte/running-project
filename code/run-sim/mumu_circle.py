"""每秒发送一个圆形定位点，默认半径 38 米；只控制所选 MuMu 本地实例。"""
import argparse
import datetime
import json
import math
from pathlib import Path
import sys
import time

import lab
from mumu_runtime import Device, WriterLease, cli_main

EARTH_RADIUS_M = 6371008.8


def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2-p1, math.radians(lon2-lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*EARTH_RADIUS_M*math.asin(math.sqrt(min(1, max(0, a))))


def circle_point(lat, lon, radius, phase):
    """Destination on a sphere; every point is exactly radius from the center."""
    angular = radius / EARTH_RADIUS_M
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1)*math.cos(angular) + math.cos(p1)*math.sin(angular)*math.cos(phase))
    l2 = l1 + math.atan2(math.sin(phase)*math.sin(angular)*math.cos(p1),
                         math.cos(angular)-math.sin(p1)*math.sin(p2))
    return math.degrees(p2), (math.degrees(l2)+180)%360-180


def chord_phase_increment(radius, chord_m):
    """Spherical law of cosines in stable half-angle form, exact geodesic chord."""
    denominator = math.sin(radius/EARTH_RADIUS_M)
    ratio = math.sin(chord_m/(2*EARTH_RADIUS_M))/denominator
    if not 0 <= ratio <= 1:
        raise ValueError('每秒位移不能超过圆的直径。')
    return 2*math.asin(ratio)


def verify_geometry(lat=0.0, lon=0.0, radius=38, speed=2.6, points=240):
    delta = chord_phase_increment(radius, speed)
    samples = [circle_point(lat, lon, radius, i*delta) for i in range(points)]
    radii = [haversine_m(lat, lon, *point) for point in samples]
    chords = [haversine_m(*a, *b) for a, b in zip(samples, samples[1:])]
    return {'center_lat': lat, 'center_lon': lon, 'radius_m': radius, 'speed_m_s': speed,
        'points': points, 'interval_s': 1, 'min_radius_m': min(radii), 'max_radius_m': max(radii),
        'max_radius_error_m': max(abs(value-radius) for value in radii),
        'min_consecutive_distance_m': min(chords), 'max_consecutive_distance_m': max(chords),
        'max_speed_error_m_s': max(abs(value-speed) for value in chords)}


def default_center(radius=38, phase=0):
    path = lab.BASE / 'run-route.json'
    route = json.loads(path.read_text(encoding='utf-8'))
    first = route['points'][0]
    # The saved point is the starting position, not the circle center. Put the
    # center behind it so sequence zero does not teleport one radius away.
    return circle_point(float(first['lat']), float(first['lon']), radius,
                        math.radians(phase) + math.pi)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=int, default=0)
    parser.add_argument('--duration', type=float, default=60)
    parser.add_argument('--radius', type=float, default=38)
    parser.add_argument('--speed', type=float, default=3.3333333333333335)
    parser.add_argument('--lat', type=float)
    parser.add_argument('--lon', type=float)
    parser.add_argument('--phase', type=float, default=0, help='初始方位角，单位度')
    parser.add_argument('--verify-only', action='store_true', help='仅校验几何，不连接或写入模拟器')
    args = parser.parse_args()
    if (args.lat is None) != (args.lon is None):
        parser.error('--lat 与 --lon 必须同时提供。')
    if args.lat is None:
        args.lat, args.lon = default_center(args.radius, args.phase)
    if not 0 < args.duration <= 3600 or not 37 <= args.radius <= 38.5 or not 0 <= args.speed <= 15:
        parser.error('时长应为 (0,3600] 秒，半径为 [37,38.5] 米，速度为 [0,15] m/s。')
    if not -85 <= args.lat <= 85 or not -180 <= args.lon <= 180 or not math.isfinite(args.phase):
        parser.error('坐标或初始角度无效。')
    geometry = verify_geometry(args.lat, args.lon, args.radius, args.speed)
    if args.verify_only:
        print(json.dumps(geometry, ensure_ascii=False, indent=2))
        return
    from mumu_start_position import load_profile, route_reviewed, verify
    if not route_reviewed(load_profile()):
        raise RuntimeError('当日地图布局尚未复核，拒绝默认圆形移动；--verify-only 可离线校验几何。')
    device = Device(args.index)
    device.connect()
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    evidence = lab.REPORTS / ('mumu-circle-' + stamp + '.jsonl')
    evidence.parent.mkdir(parents=True, exist_ok=True)
    phase = math.radians(args.phase)
    increment = chord_phase_increment(args.radius, args.speed)
    # Advance exactly one planned 1s chord per successful command. After a late
    # call or reconnect, pause the simulation clock rather than jumping position.
    # Timing evidence remains explicit: host scheduling is not real-time.
    with WriterLease('circle') as writer, evidence.open('w', encoding='utf-8', newline='\n') as output:
        verify(args.index, require_reviewed_route=True, writer_lease=writer)
        output.write(json.dumps({'kind': 'config', 'index': args.index, 'geometry': geometry,
            'duration_s': args.duration, 'coordinate_system': 'MuMu CLI input',
            'reconnect_policy': 'pause_position_clock; no queued catch-up writes'}, ensure_ascii=False)+'\n')
        output.flush()
        started, due, count = time.monotonic(), time.monotonic(), 0
        previous_end, previous_point = None, None
        while count <= math.floor(args.duration):
            time.sleep(max(0, due-time.monotonic()))
            if not device.ready():
                output.write(json.dumps({'kind': 'reconnect', 'elapsed_s': time.monotonic()-started,
                    'reason': 'MuMu selected instance or ADB is not ready'}, ensure_ascii=False)+'\n')
                output.flush()
                device.connect(timeout=180)
                due = time.monotonic()+1
                continue
            lat, lon = circle_point(args.lat, args.lon, args.radius, phase+count*increment)
            before = time.monotonic()
            try:
                response = writer.location(args.index, lat, lon)
            except RuntimeError as error:
                if writer.pending is not None:
                    raise  # An uncertain write must not be retried or overlapped.
                output.write(json.dumps({'kind': 'reconnect', 'elapsed_s': time.monotonic()-started,
                    'error': str(error)}, ensure_ascii=False)+'\n'); output.flush()
                device.connect(timeout=180)
                due = time.monotonic()+1
                continue
            end = time.monotonic()
            distance = haversine_m(*previous_point, lat, lon) if previous_point else None
            row = {'kind': 'location_command', 'sequence': count, 'planned_elapsed_s': count,
                'host_elapsed_s': before-started, 'lat': lat, 'lon': lon,
                'radius_m': haversine_m(args.lat, args.lon, lat, lon),
                'consecutive_distance_m': distance, 'call_duration_ms': (end-before)*1000,
                'observed_completion_interval_s': end-previous_end if previous_end is not None else None,
                'lateness_ms': max(0, before-due)*1000, 'response': response}
            output.write(json.dumps(row, ensure_ascii=False)+'\n'); output.flush()
            if count%10 == 0:
                print(f'圆形定位：{count:.0f}/{args.duration:.0f} 秒，半径 {args.radius:g} 米，速度 {args.speed:g} m/s。', flush=True)
            previous_point, previous_end = (lat, lon), end
            count += 1
            due += 1
            if end > due:
                due = end+1  # Never send burst catch-up commands.
    print('定位证据：' + str(evidence))


if __name__ == '__main__':
    sys.exit(cli_main(main))
