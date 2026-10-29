"""Restore the existing local Step Lab sensor mode after its instrumentation exits."""
import argparse
import datetime
import json
import socket

import step_sim as step


def recover(supervisor):
    if supervisor.active():
        raise RuntimeError('Exact Step Lab instrumentation is still active; no mode change was attempted.')
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    prefix = step.lab.REPORTS/('step-recover-'+stamp)
    supervisor.prefix = prefix
    before = supervisor.adb('shell', 'dumpsys', 'sensorservice')
    prefix.with_suffix('.before.txt').write_text(before, encoding='utf-8')
    report = dict(initial_mode=step.mode(before), helper_active=False,
                  install_attempted=False, permissions_changed=False,
                  emulator_restarted=False, normal_verified=False)
    try:
        supervisor.restore()  # exact/empty test allowlist only; two verified attempts
    except RuntimeError as error:
        report['restore_error'] = str(error)
    after = supervisor.adb('shell', 'dumpsys', 'sensorservice')
    prefix.with_suffix('.after.txt').write_text(after, encoding='utf-8')
    report.update(final_mode=step.mode(after), normal_verified=step.mode(after)=='NORMAL')
    prefix.with_suffix('.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(report, report=str(prefix.with_suffix('.json'))), ensure_ascii=False), flush=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=int, default=0)
    args = parser.parse_args(argv)
    supervisor = step.StepSupervisor(step.arguments(['--index', str(args.index)]))
    # Same host lock as the running supervisor: never reset underneath its client.
    with socket.socket() as lock:
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            lock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        lock.bind(('127.0.0.1', 15582)); lock.listen(1)
        if not supervisor.ready():
            raise RuntimeError('Existing local MuMu device is offline; no emulator was started.')
        report = recover(supervisor)
        return 0 if report['normal_verified'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
