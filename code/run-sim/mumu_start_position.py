"""MuMu 启动位置守卫：分别核验CLI输入与原生GPS参考，再允许启动目标应用。"""
import argparse
import contextlib
import datetime
import json
import math
from pathlib import Path
import re
import sys
import time

import lab
from mumu_circle import haversine_m
from mumu_runtime import Device, WriterLease, cli_main, read_gps, save_json

PROFILE = lab.BASE / 'position-profile.json'


def load_profile(path=PROFILE):
    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema_version') != 1 or data.get('runtime') != 'MuMu':
        raise RuntimeError('启动位置配置版本或运行环境不匹配。')
    if (data.get('instance_index'), data.get('target_package'), data.get('version_name'), data.get('version_code')) != (0, lab.APP, '7.4.0', 408):
        raise RuntimeError('启动配置必须锁定 MuMu 实例0与运动世界7.4.0/408。')
    if data.get('instance_created_timestamp') is None:
        raise RuntimeError('启动配置实例身份与本次已验证的MuMu实例不符。')
    if data.get('route_point_index') != 0 or data.get('waypoint_point_index') != 0:
        raise RuntimeError('启动配置必须核对路线和点位的第一个点。')
    inp, reference = data['cli_input'], data['native_gps_reference']
    if inp.get('coordinate_system') != 'mumu_cli_observed_BD09' or reference.get('coordinate_system') != 'Android GPS provider observed WGS84':
        raise RuntimeError('启动位置必须分别声明MuMu输入和Android原生GPS参考坐标系。')
    for point in [inp, reference]:
        if not -85 <= float(point['lat']) <= 85 or not -180 <= float(point['lon']) <= 180:
            raise RuntimeError('启动配置坐标无效。')
    if not 1 <= float(data['maximum_position_error_m']) <= 20 or not 1 <= float(data['maximum_fix_age_s']) <= 5 or int(data['minimum_satellites']) < 1:
        raise RuntimeError('启动校验阈值不得放宽到20米、5秒或零卫星之外。')
    for file_key, index_key in [('route_file', 'route_point_index'), ('waypoint_file', 'waypoint_point_index')]:
        file_path = (path.parent / data[file_key]).resolve()
        if file_path.parent != path.parent:
            raise RuntimeError('启动坐标配置只能引用同目录路线文件。')
        fixture = json.loads(file_path.read_text(encoding='utf-8'))
        if fixture.get('coordinate_system') != inp['coordinate_system']:
            raise RuntimeError(file_path.name + ' 与启动输入的坐标系不一致。')
        point = fixture['points'][int(data[index_key])]
        if file_key == 'waypoint_file' and point.get('name') != data.get('waypoint_name'):
            raise RuntimeError('启动配置点名与waypoints点名不一致，拒绝使用旧点。')
        if abs(float(point['lat'])-float(inp['lat'])) > 1e-8 or abs(float(point['lon'])-float(inp['lon'])) > 1e-8:
            raise RuntimeError(file_path.name + ' 的起点与固定启动输入不一致，拒绝选择默认位置。')
    return data


def evaluate(gps, profile):
    expected = profile['native_gps_reference']
    distance = None
    if gps.get('lat') is not None and gps.get('lon') is not None:
        distance = haversine_m(float(expected['lat']), float(expected['lon']), gps['lat'], gps['lon'])
    count, age = gps.get('satellites_in_fix_extras'), gps.get('last_fix_age_s')
    reasons = []
    if count is None or count < profile['minimum_satellites']:
        reasons.append('卫星数未达到要求')
    if age is None or age > profile['maximum_fix_age_s']:
        reasons.append('定位过期或缺少时间')
    if not gps.get('gps_started') or not gps.get('gps_enabled'):
        reasons.append('GPS未启用或未保持位置请求')
    if distance is None or distance > profile['maximum_position_error_m']:
        reasons.append('位置偏离固定启动点')
    return {'passed': not reasons, 'distance_to_native_reference_m': distance,
        'satellites': count, 'fix_age_s': age, 'reasons': reasons}


def route_reviewed(profile):
    layout = profile.get('layout_validation', {})
    local_date = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).date().isoformat()
    return (profile.get('review_required') is False and layout.get('verified') is True
        and layout.get('validated_local_date') == local_date and bool(layout.get('layout_id')))


def active_run(device):
    # A paused RunRandomActivity can still own a running session. Treat any live
    # ActivityRecord for it conservatively; do not depend only on foreground focus.
    text = device.adb('shell', 'dumpsys', 'activity', 'activities')
    matches = [line.strip() for line in text.splitlines()
        if 'RunRandomActivity' in line and any(token in line for token in ['ActivityRecord{', 'mResumedActivity', 'topResumedActivity', 'mFocusedApp'])]
    return matches


def validate_identity(device, info, profile):
    if device.index != profile['instance_index'] or str(info.get('index')) != str(profile['instance_index']):
        raise RuntimeError('所选MuMu实例不是已保存的实例0。')
    if info.get('created_timestamp') != profile['instance_created_timestamp']:
        raise RuntimeError('MuMu实例已重建或选错，必须重新核验启动位置配置。')
    package = device.adb('shell', 'dumpsys', 'package', profile['target_package'])
    code = re.search(r'\bversionCode=(\d+)\b', package)
    name = re.search(r'\bversionName=([^\s]+)', package)
    if not code or not name or (int(code[1]), name[1]) != (profile['version_code'], profile['version_name']):
        raise RuntimeError('当前实例中的目标应用不是7.4.0/408，拒绝启动位置写入。')


def verify(index=0, check_only=False, timeout=20, profile_path=PROFILE, report_path=None,
           require_reviewed_route=False, writer_lease=None):
    profile = load_profile(profile_path)
    if index != profile['instance_index']:
        raise RuntimeError('启动位置配置只允许已验证的MuMu实例0。')
    reviewed = route_reviewed(profile)
    if require_reviewed_route and not reviewed:
        raise RuntimeError('当前日期/地图布局尚未复核，已拒绝自动移动与路线启动。GPS位置匹配不能替代必经点匹配。')
    device = Device(index)
    info = device.connect(timeout=20 if check_only else 120)
    corrected = False
    scope = contextlib.nullcontext(writer_lease) if writer_lease is not None else WriterLease(
        'startup_position_check' if check_only else 'startup_position', read_only=check_only)
    with scope as writer:
        validate_identity(device, info, profile)
        before, _ = read_gps(device)
        if not before.get('gps_enabled'):
            raise RuntimeError('测试实例的系统定位已关闭，请由你在Android设置中开启；工具不会代办隐私或权限设置。')
        state = evaluate(before, profile)
        after = before
        run_records = active_run(device)
        if not check_only:
            # A requester activates native GPS at its retained position. It does
            # not change coordinates, even when today's route is unreviewed.
            from mumu_fix_gnss import ensure_observer
            ensure_observer(device)
            request_deadline = time.monotonic()+min(timeout, 4)
            while True:
                after, _ = read_gps(device)
                state = evaluate(after, profile)
                if state['passed'] or time.monotonic() >= request_deadline:
                    break
                time.sleep(.5)
        if not state['passed']:
            if check_only:
                print('只读检查未通过：' + '；'.join(state['reasons']), flush=True)
            elif not reviewed:
                raise RuntimeError('当前地图布局待复核且GPS参考未匹配，已拒绝纠正旧点位。请先核实当前启动点配置。')
            elif run_records:
                raise RuntimeError('检测到活动RunRandomActivity，拒绝自动跳点。请结束当前测试会话后重试。')
            else:
                # Re-check immediately before writing while the writer lock remains
                # held; a second tool cannot initialize a competing GPS source.
                if active_run(device):
                    raise RuntimeError('准备纠正位置时检测到RunRandomActivity，已取消跳点。')
                point = profile['cli_input']
                writer.location(index, float(point['lat']), float(point['lon']))
                corrected = True
                deadline = time.monotonic()+timeout
                old_fix = before.get('last_fix_elapsed_s')
                while True:
                    after, _ = read_gps(device)
                    state = evaluate(after, profile)
                    if state['passed'] and after.get('last_fix_elapsed_s') != old_fix:
                        break
                    if time.monotonic() >= deadline:
                        state['passed'] = False
                        state['reasons'].append('纠正后未在期限内读回新的合格GPS定位')
                        break
                    time.sleep(.5)
        # Always collect a second fresh observation. Matching a stale cached point
        # is insufficient; monotonic timestamps must advance during the check.
        if state['passed']:
            first_fix = after.get('last_fix_elapsed_s')
            deadline = time.monotonic()+min(timeout, 6)
            while True:
                time.sleep(.5)
                second, _ = read_gps(device)
                second_state = evaluate(second, profile)
                if second_state['passed'] and second.get('last_fix_elapsed_s') != first_fix:
                    after, state = second, second_state
                    break
                if time.monotonic() >= deadline:
                    after, state = second, second_state
                    state['passed'] = False
                    state['reasons'].append('未观测到两次持续更新的合格GPS定位')
                    break
        result = {'created_local': datetime.datetime.now().astimezone().isoformat(),
            'name': profile['name'], 'check_only': check_only, 'corrected': corrected,
            'index': index, 'instance_created_timestamp': info['created_timestamp'], 'serial': device.serial,
            'target_package': profile['target_package'], 'version_name': profile['version_name'],
            'version_code': profile['version_code'], 'cli_input': profile['cli_input'],
            'native_gps_reference': profile['native_gps_reference'],
            'before': before, 'after': after, 'active_run_records': run_records,
            'passed_location': state['passed'], 'passed_route': state['passed'] and reviewed,
            'route_reviewed': reviewed, 'review_required': not reviewed,
            'review_reason': profile.get('review_reason'), **state}
        if report_path:
            save_json(Path(report_path), result)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        if not result['passed']:
            raise RuntimeError('启动位置未通过校验，已阻止后续自动启动：' + '；'.join(state['reasons']))
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=int, default=0)
    parser.add_argument('--check-only', action='store_true', help='仅检查，不启动服务、启用定位、写入位置或打开APP')
    parser.add_argument('--timeout', type=float, default=20)
    parser.add_argument('--report', type=Path, help='可选：保存本机JSON证据；不影响设备状态')
    parser.add_argument('--require-reviewed-route', action='store_true', help='强制要求当日地图布局复核；未复核时禁止自动移动')
    args = parser.parse_args()
    if not 5 <= args.timeout <= 120:
        parser.error('--timeout 应为5~120秒。')
    verify(args.index, args.check_only, args.timeout, report_path=args.report,
           require_reviewed_route=args.require_reviewed_route)


if __name__ == '__main__':
    sys.exit(cli_main(main))
