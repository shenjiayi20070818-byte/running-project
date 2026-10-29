"""Check the original target APK on an explicitly selected Android test device."""
import argparse
import datetime
import json
from pathlib import Path
import re
import subprocess
import sys
import time

import lab


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', required=True, help='Exact serial from adb devices -l')
    parser.add_argument('--launch', action='store_true', help='Restart the installed test app and observe it for 12 seconds')
    parser.add_argument('--observe', action='store_true', help='Observe the running app without restarting it')
    parser.add_argument('--name', default='target-check')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', args.name):
        parser.error('--name accepts letters, digits, dot, hyphen and underscore')
    if args.serial.startswith('-'):
        parser.error('invalid serial')
    base = [lab.executable('adb'), '-s', args.serial]

    def adb(*command, binary=False, timeout=30):
        return lab.run([*base, *command], binary=binary, timeout=timeout, check=False)

    package = adb('shell', 'dumpsys', 'package', lab.APP)
    match = re.search(r'versionCode=(\d+)', package)
    version = re.search(r'versionName=([^\s]+)', package)
    if not match or not version or (match[1], version[1]) != ('408', '7.4.0'):
        raise RuntimeError('The selected device does not have target version 7.4.0 / 408 installed.')
    folder = lab.REPORTS
    folder.mkdir(parents=True, exist_ok=True)
    prefix = folder / args.name
    prefix.with_suffix('.package.txt').write_text(package, encoding='utf-8')
    report = {'serial': args.serial, 'package': lab.APP, 'version': version[1],
              'version_code': int(match[1]), 'launch_requested': args.launch,
              'checked_at': datetime.datetime.now().astimezone().isoformat()}
    for key, prop in [('api', 'ro.build.version.sdk'), ('abis', 'ro.product.cpu.abilist'),
                      ('native_bridge', 'ro.dalvik.vm.native.bridge')]:
        report[key] = adb('shell', 'getprop', prop).strip()
    cpu = re.search(r'primaryCpuAbi=([^\s]+)', package)
    report['installed_abi'] = cpu[1] if cpu else None
    if args.launch or args.observe:
        log_path = prefix.with_suffix('.logcat.txt')
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        with log_path.open('wb') as output:
            process = subprocess.Popen([*base, 'logcat', '-T', '1', '-v', 'threadtime'],
                                       stdout=output, stderr=subprocess.STDOUT,
                                       env=lab.env(), creationflags=flags)
            samples = []
            try:
                if args.launch:
                    adb('shell', 'am', 'force-stop', lab.APP)
                    report['start_output'] = adb('shell', 'am', 'start', '-n',
                        lab.APP + '/' + lab.APP + '.mvi.splash.SplashActivity')
                for _ in range(6):
                    time.sleep(2)
                    samples.append(adb('shell', 'pidof', lab.APP).strip())
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
        report['pid_samples_every_2s'] = samples
        text = log_path.read_text(encoding='utf-8', errors='replace')
        # PID-filter the captured log so another application's crash is not attributed to this one.
        app_pids = set(re.findall(r'Start proc (\d+):' + re.escape(lab.APP), text))
        app_pids.update(x for sample in samples for x in sample.split() if x.isdigit())
        relevant = []
        for line in text.splitlines():
            row = re.match(r'\S+\s+\S+\s+(\d+)\s+', line)
            if row and row[1] in app_pids:
                relevant.append(line)
        evidence = '\n'.join(relevant)
        report['crash_evidence'] = [line for line in relevant if
            'FATAL EXCEPTION' in line or 'UnsatisfiedLinkError' in line or
            'Fatal signal' in line or ('nesec' in line and 'dlopen failed' in line)]
        report['jni_load_error'] = 'MyJni.load' in evidence and 'UnsatisfiedLinkError' in evidence
        # A native bridge can add a helper process under the same package name.
        # Require a continuously present PID; an added helper is not a restart.
        stable_pids = set.intersection(*(set(sample.split()) for sample in samples)) if samples else set()
        report['continuously_present_pids'] = sorted(stable_pids)
        report['process_stable_12s'] = bool(stable_pids)
        if report['crash_evidence']:
            report['startup'] = 'failed'
        elif report['process_stable_12s']:
            report['startup'] = 'process_survived_12s_requires_screen_review'
        else:
            report['startup'] = 'not_confirmed'
        activity = adb('shell', 'dumpsys', 'activity', 'activities')
        prefix.with_suffix('.activity.txt').write_text(activity, encoding='utf-8')
        report['resumed_activity'] = [x.strip() for x in activity.splitlines()
            if 'mResumedActivity:' in x or 'topResumedActivity=' in x]
        screenshot = adb('exec-out', 'screencap', '-p', binary=True)
        if screenshot.startswith(b'\x89PNG\r\n\x1a\n'):
            prefix.with_suffix('.png').write_bytes(screenshot)
    prefix.with_suffix('.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if report.get('startup') in ('failed', 'not_confirmed') else 0


if __name__ == '__main__':
    raise SystemExit(main())
