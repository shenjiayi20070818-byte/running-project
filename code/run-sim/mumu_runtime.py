"""MuMu 本地实验运行支持：固定实例、串行调用和可恢复的 GPS 写入锁。"""
from __future__ import annotations

import contextlib
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import uuid

import lab
import mumu

GPS_LOCK_PORT = 15581
STATE_DIR = lab.WORK / 'runtime'
INFLIGHT = STATE_DIR / 'mumu-gps-inflight.json'
LAST_INPUT = STATE_DIR / 'mumu-last-location-input.json'


def save_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('w', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def manager_call(*args, timeout=20, lease=None):
    """One CLI call. Location writes require WriterLease; timeout never retries writes."""
    is_location = 'location' in args and 'tool' in args
    if is_location and lease is None:
        raise RuntimeError('定位写入必须持有全局 WriterLease。')
    if lease is not None:
        lease._before_call(args, timeout)
    options = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=lab.env())
    if os.name == 'nt':
        # The child retains the exclusive socket if this Python process is killed.
        # No GUI automation: this is process/handle lifetime management for the CLI.
        options.update(creationflags=subprocess.CREATE_NO_WINDOW, close_fds=False)
    elif lease is not None:
        options.update(pass_fds=(lease.socket.fileno(),))
    process = None
    completed = False
    try:
        process = subprocess.Popen([str(mumu.manager_path()), *map(str, args)], **options)
        if lease is not None:
            lease._child_started(process.pid)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            completed = True
        except subprocess.TimeoutExpired:
            # A command may already be queued in MuMu. Keep the recovery fence even
            # after killing its CLI process; the next writer must wait it out.
            process.kill()
            process.communicate(timeout=10)
            raise RuntimeError('MuMu 命令超时；已停止该 CLI 子进程，并保留恢复等待标记。')
        text = (stdout + stderr).decode('utf-8-sig', errors='replace').strip()
        if process.returncode:
            raise RuntimeError('MuMu CLI 执行失败：' + text[:1200])
        try:
            value = json.loads(text)
        except ValueError as error:
            raise RuntimeError('MuMu 返回格式异常：' + text[:1200]) from error
        if not isinstance(value, dict) or value.get('errcode', 0):
            raise RuntimeError('MuMu 返回错误：' + text[:1200])
        return value
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=10)
        if lease is not None and completed:
            lease._completed_call()


class WriterLease:
    """Common port15581 guard, inherited by direct CLI children, plus crash fence.

    Every location writer must use this API, including older route scripts. Unknown
    stale state fails closed; timed-out/abandoned calls impose deadline+5s grace.
    This prevents concurrent host CLI writers. It cannot cancel work already queued
    inside MuMu; the fence waits that bounded command window before a new writer.
    """
    def __init__(self, name='gps', recovery_timeout=40, read_only=False):
        self.name = name
        self.recovery_timeout = recovery_timeout
        self.token = uuid.uuid4().hex
        self.read_only = read_only
        self.socket = None
        self.pending = None

    def __enter__(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            self.socket.bind(('127.0.0.1', GPS_LOCK_PORT))
            self.socket.listen(1)
            self.socket.set_inheritable(True)
            if INFLIGHT.exists():
                try:
                    state = json.loads(INFLIGHT.read_text(encoding='utf-8'))
                    wait_until = float(state['recovery_after_wall_s'])
                    if state.get('format') != 1 or not math.isfinite(wait_until):
                        raise ValueError('unrecognized state')
                except (OSError, KeyError, ValueError, TypeError) as error:
                    raise RuntimeError('GPS 恢复标记无法校验，已拒绝写入：' + str(INFLIGHT)) from error
                delay = max(0, wait_until - time.time())
                if delay > self.recovery_timeout:
                    raise RuntimeError('前一条 GPS 命令仍在恢复等待期，稍后重试。标记：' + str(INFLIGHT))
                if delay:
                    print(f'等待上一条 GPS 命令结束及隔离期：{delay:.1f} 秒。', flush=True)
                    time.sleep(delay)
                if not self.read_only:
                    INFLIGHT.unlink(missing_ok=True)
            return self
        except OSError as error:
            self.socket.close()
            self.socket = None
            raise RuntimeError('已有 GPS 写入程序或其未退出的子进程占用端口 15581。') from error
        except BaseException:
            self.socket.close()
            self.socket = None
            raise

    def _before_call(self, args, timeout):
        if self.socket is None:
            raise RuntimeError('GPS 写入锁尚未取得。')
        if self.read_only:
            raise RuntimeError('只读定位锁禁止调用定位写入。')
        if self.pending is not None:
            raise RuntimeError('上一条 CLI 命令状态未确定，已拒绝后续定位。')
        self.pending = {'format': 1, 'token': self.token, 'owner_pid': os.getpid(),
            'name': self.name, 'issued_wall_s': time.time(),
            'recovery_after_wall_s': time.time() + timeout + 5,
            'command': list(map(str, args)), 'child_pid': None}
        save_json(INFLIGHT, self.pending)

    def _child_started(self, pid):
        self.pending['child_pid'] = pid
        save_json(INFLIGHT, self.pending)

    def _completed_call(self):
        INFLIGHT.unlink(missing_ok=True)
        self.pending = None

    def location(self, index, lat, lon, timeout=15):
        if not -85 <= lat <= 85 or not -180 <= lon <= 180:
            raise ValueError('经纬度超出本地实验范围。')
        response = manager_call('control', '-v', str(index), 'tool', 'location',
            '-lat', f'{lat:.9f}', '-lon', f'{lon:.9f}', timeout=timeout, lease=self)
        save_json(LAST_INPUT, {'index': index, 'lat': lat, 'lon': lon,
            'wall_s': time.time(), 'source': self.name,
            'datum': 'MuMu CLI input; observed BD09-like conversion, not an official guarantee'})
        return response

    def __exit__(self, *exc):
        if self.socket is not None:
            self.socket.close()
            self.socket = None


class Device:
    """Only the selected local MuMu instance, rediscovered after Android restarts."""
    def __init__(self, index=0):
        if index < 0:
            raise ValueError('实例索引必须非负。')
        self.index, self.serial = index, None

    def connect(self, timeout=120):
        deadline = time.monotonic() + timeout
        last_error = ''
        announced = False
        while True:
            try:
                info = manager_call('info', '-v', str(self.index), timeout=10)
                host, port = info.get('adb_host_ip'), info.get('adb_port')
                if host not in (None, '127.0.0.1', 'localhost', '::1'):
                    raise RuntimeError('拒绝连接非本机 ADB 地址。')
                if info.get('is_process_started') and host and port:
                    port = int(port)
                    if not 1 <= port <= 65535:
                        raise RuntimeError('MuMu 返回无效 ADB 端口。')
                    serial = f'[{host}]:{port}' if ':' in host else f'{host}:{port}'
                    lab.run([lab.executable('adb'), 'connect', serial], timeout=8, check=False)
                    boot = lab.run([lab.executable('adb'), '-s', serial, 'shell',
                        'getprop', 'sys.boot_completed'], timeout=8, check=False)
                    if boot.strip() == '1':
                        self.serial = serial
                        return info
                last_error = '所选 MuMu 实例尚未启动完成。'
            except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
                last_error = str(error)
            if time.monotonic() >= deadline:
                raise RuntimeError('MuMu 重连等待超时：' + last_error)
            if not announced:
                print('等待 MuMu 测试实例恢复连接……', flush=True)
                announced = True
            time.sleep(min(2, max(0, deadline - time.monotonic())))

    def adb(self, *args, timeout=20, check=True):
        if self.serial is None:
            self.connect()
        return lab.run([lab.executable('adb'), '-s', self.serial, *args], timeout=timeout, check=check)

    def ready(self):
        """A bounded health check; stale ports never select another emulator."""
        try:
            info = manager_call('info', '-v', str(self.index), timeout=5)
            host, port = info.get('adb_host_ip'), info.get('adb_port')
            if host not in ('127.0.0.1', 'localhost', '::1') or not port:
                return False
            serial = f'[{host}]:{int(port)}' if ':' in host else f'{host}:{int(port)}'
            if not info.get('is_android_started') or serial != self.serial:
                return False
            return self.adb('shell', 'getprop', 'sys.boot_completed', timeout=5, check=False).strip() == '1'
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
            return False


def android_duration_seconds(value):
    if value == '0':
        return 0.0
    matches = re.findall(r'(\d+(?:\.\d+)?)(ms|d|h|m|s)', value.lstrip('+'))
    if not matches:
        return None
    factors = {'d': 86400, 'h': 3600, 'm': 60, 's': 1, 'ms': .001}
    return sum(float(number) * factors[unit] for number, unit in matches)


def gps_snapshot(text, uptime_s=None):
    match = re.search(r'^\s{4}gps provider:\s*\n(.*?)(?=^\s{4}\S|^\s{2}\S|\Z)',
                      text.replace('\r', ''), re.M | re.S)
    block = match[1] if match else ''
    location = re.search(r'last location=(Location\[.*)', block)
    line = location[1] if location else ''
    sat = re.search(r'\bsatellites=(\d+)', line)
    fix = re.search(r'Location\[gps\s+([-\d.]+),([-\d.]+)', line)
    elapsed = re.search(r'\bet=([^\s]+)', line)
    fix_elapsed = android_duration_seconds(elapsed[1]) if elapsed else None
    age = max(0, uptime_s-fix_elapsed) if uptime_s is not None and fix_elapsed is not None else None
    return {'satellites_in_fix_extras': int(sat[1]) if sat else None,
        'satellite_measurement_source': 'dumpsys GPS last-location extras; not a GnssStatus callback count',
        'gps_started': bool(re.search(r'mStarted=true', block)),
        'gps_request_active': bool(re.search(r'service: ProviderRequest\[(?!OFF)', block)),
        'gps_enabled': bool(re.search(r'enabled=true', block)),
        'lat': float(fix[1]) if fix else None, 'lon': float(fix[2]) if fix else None,
        'last_fix_elapsed_s': fix_elapsed, 'last_fix_age_s': age, 'last_location': line,
        'raw_provider': block}


def read_gps(device):
    text = device.adb('shell', 'dumpsys', 'location')
    try:
        uptime_s = float(device.adb('shell', 'cat', '/proc/uptime').split()[0])
    except (ValueError, IndexError):
        uptime_s = None
    return gps_snapshot(text, uptime_s), text


def cli_main(function):
    try:
        return function() or 0
    except KeyboardInterrupt:
        print('已停止。', flush=True)
        return 130
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        print('错误：' + str(error), flush=True)
        return 1
