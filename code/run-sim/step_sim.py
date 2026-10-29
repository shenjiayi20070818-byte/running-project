"""Reconnect Step Lab without launching MuMu or changing app permissions."""
import argparse
import datetime
import json
import re
import socket
import subprocess
import sys
import time
import uuid

import lab
import mumu

ALLOWLIST = 'o00OoOO0.OooOOOO'
HELPER = ALLOWLIST + '.sensorlab'


def mode(text):
    found = re.search(r'^Mode\s*:\s*([A-Z_]+)', text, re.M)
    return found[1] if found else None


def instrumentation_active(text):
    """Parse the supported Android process dump, never an echoed invalid command."""
    if (not re.search(r'^ACTIVITY MANAGER RUNNING PROCESSES\b', text, re.M)
            or re.search(r'Bad activity command|Unknown command|Permission Denial|Permission denied', text, re.I)):
        raise RuntimeError('Cannot verify instrumentation from a valid Android process dump.')
    lines = text.splitlines()
    for index, line in enumerate(lines):
        header = re.match(r'^([ \t]*)Active instrumentation:[ \t]*$', line)
        if not header:
            continue
        indentation = len(header[1])
        section = []
        for row in lines[index+1:]:
            if row.strip() and len(row)-len(row.lstrip()) <= indentation:
                break
            section.append(row)
        section = '\n'.join(section)
        own = re.findall(r'mClass=ComponentInfo\{' + re.escape(HELPER)
                         + r'/[^}]+\}[ \t]+mFinished=(true|false)\b', section)
        if own:
            return 'false' in own
        if HELPER in section:
            raise RuntimeError('Step Lab instrumentation is present but its state cannot be parsed.')
    return False


def arguments(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    for key, default in [('duration', 10), ('cadence', 180), ('cadence-variation', 6),
                         ('amplitude', 3.2), ('rate', 50)]:
        p.add_argument('--'+key, type=float, default=default)
    p.add_argument('--show-ui', action='store_true')
    p.add_argument('--no-progress', action='store_true')
    p.add_argument('--stop', action='store_true')
    p.add_argument('--index', type=int, default=0)
    p.add_argument('--attach-run-id', help='Monitor an existing matching test without install or a new instrumentation.')
    a = p.parse_args(argv)
    if (not 1 <= a.duration <= 1800 or not 0 <= a.cadence <= 240
            or not 0 <= a.cadence_variation <= 18 or not 0 <= a.amplitude <= 8
            or not 2 <= a.rate <= 100 or a.index < 0):
        p.error('Unsupported duration, cadence, amplitude, rate or index.')
    if a.attach_run_id and (a.stop or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', a.attach_run_id)):
        p.error('Invalid attach run ID or conflicting --stop.')
    return a


class StepSupervisor:
    def __init__(self, args):
        self.args, self.serial, self.run_id = args, None, None
        self.process, self.output, self.prefix = None, None, None
        self.completed, self.segment_elapsed, self.segment_duration = 0.0, 0.0, 0.0
        self.started, self.previous_progress, self.previous_status = 0.0, -1.0, None
        self.attach_pending, self.attached = args.attach_run_id, False
        lab.REPORTS.mkdir(parents=True, exist_ok=True)

    def status(self, state, detail=''):
        row = dict(state=state, detail=detail, serial=self.serial, run_id=self.run_id,
                   completed_seconds=self.completed, measured_seconds=self.completed+self.segment_elapsed,
                   requested_seconds=self.args.duration,
                   updated_at=datetime.datetime.now().astimezone().isoformat())
        (lab.REPORTS/'step-sim-status.json').write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding='utf-8')
        if (state, detail) != self.previous_status:
            print(json.dumps(row, ensure_ascii=False), flush=True)
            self.previous_status = state, detail

    def adb(self, *args, timeout=15, check=True):
        return lab.run([lab.executable('adb'), '-s', self.serial, *args], timeout=timeout, check=check)

    def ready(self):
        """Inspect/reconnect an existing device; no emulator lifecycle commands."""
        try:
            info = mumu.manager('info', '-v', str(self.args.index))
            if not info.get('is_process_started') or not info.get('adb_port'):
                return False
            if str(info.get('index', self.args.index)) != str(self.args.index):
                return False
            host = info.get('adb_host_ip')
            if host not in ('127.0.0.1', 'localhost', '::1'):
                return False
            self.serial = f'{host}:{int(info["adb_port"])}'
            try:
                if self.adb('shell', 'getprop', 'sys.boot_completed', timeout=5).strip() == '1':
                    return True
            except (RuntimeError, OSError, subprocess.TimeoutExpired):
                pass
            lab.run([lab.executable('adb'), 'connect', self.serial], timeout=10, check=False)
            return self.adb('shell', 'getprop', 'sys.boot_completed', timeout=5).strip() == '1'
        except (RuntimeError, OSError, subprocess.TimeoutExpired):
            return False

    def active(self):
        return instrumentation_active(self.adb('shell', 'dumpsys', 'activity', 'processes', HELPER))

    def attach(self):
        """Read-only adoption; ownership is set only after all identity checks pass."""
        if not self.active():
            raise RuntimeError('The requested Step Lab instrumentation is not active; attach did not change anything.')
        progress = self.remote_json('sensor-progress.json')
        if not progress or progress.get('run_id') != self.attach_pending:
            raise RuntimeError('Active test progress does not match --attach-run-id; attach did not change anything.')
        elapsed = float(progress.get('elapsed_seconds', -1))
        if elapsed < 0 or elapsed > self.args.duration+2:
            raise RuntimeError('Specify the original total --duration when attaching.')
        candidates = list(lab.REPORTS.glob('mumu-sensor-*-'+self.attach_pending[:8]+'.before.txt'))
        if len(candidates) != 1:
            raise RuntimeError('Cannot identify exactly one original report prefix for this run.')
        prefix = candidates[0].with_name(candidates[0].name[:-len('.before.txt')])
        self.prefix, self.run_id = prefix, self.attach_pending
        self.segment_elapsed, self.segment_duration = elapsed, self.args.duration
        self.started, self.previous_progress = time.monotonic()-elapsed, -1.0
        self.attach_pending, self.attached = None, True
        self.prefix.with_suffix('.attach.json').write_text(json.dumps(dict(
            run_id=self.run_id, attached_elapsed_seconds=elapsed,
            requested_total_seconds=self.args.duration, install=False,
            new_instrumentation=False, sensor_mode_changed=False), indent=2), encoding='utf-8')
        self.status('running', 'Attached to the matching existing test; no install, new instrumentation or mode change.')

    def remote_json(self, filename):
        text = self.adb('shell', 'run-as', HELPER, 'cat', 'files/'+filename, check=False)
        try:
            return json.loads(text)
        except ValueError:
            return None

    def close_client(self):
        # Call only after confirming that no remote helper instrumentation is active.
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=10)
        self.process = None
        if self.output is not None:
            self.output.close()
            self.output = None

    def restore(self):
        if self.active():
            raise RuntimeError('Helper is still active; NORMAL restoration was not attempted underneath it.')
        before = self.adb('shell', 'dumpsys', 'sensorservice')
        if mode(before) == 'NORMAL':
            return
        found = re.search(r'^Mode[ \t]*:[ \t]*DATA_INJECTION[ \t]*:[ \t]*([^\r\n]*)', before, re.M)
        if not found or found[1].strip() not in ('', ALLOWLIST):
            raise RuntimeError('A different test owns the current SensorService mode; it was not changed.')
        for attempt in range(2):
            reply = self.adb('shell', 'dumpsys', 'sensorservice', 'enable')
            time.sleep(0.5)
            after = self.adb('shell', 'dumpsys', 'sensorservice')
            if self.prefix:
                self.prefix.with_suffix(f'.restore-{attempt+1}.txt').write_text(reply, encoding='utf-8')
                self.prefix.with_suffix('.restored.txt').write_text(after, encoding='utf-8')
            if mode(after) == 'NORMAL':
                return
        self.status('hal_restore_failed', 'HAL refuses NORMAL. Restart MuMu only after the run is idle; no automatic reboot is performed.')
        raise RuntimeError('SensorService NORMAL restoration failed after two verified attempts.')

    def prepare(self):
        text = self.adb('shell', 'dumpsys', 'package', lab.APP)
        code, version = re.search(r'versionCode=(\d+)', text), re.search(r'versionName=([^\s]+)', text)
        if not code or not version or (code[1], version[1]) != ('408', '7.4.0'):
            raise RuntimeError('Original target version 7.4.0 / 408 must be installed.')
        if self.active():
            return False
        self.restore()
        # No -g, pm grant, appops, or permission-dialog interactions.
        print(self.adb('install', '-r', str(lab.BASE.parent/'apks/mumu-step-test.apk'), timeout=120), flush=True)
        if self.active():
            raise RuntimeError('An instrumentation appeared during preparation; another one was not started.')
        packages = {x.removeprefix('package:') for x in self.adb('shell', 'pm', 'list', 'packages').splitlines()}
        if {x for x in packages if ALLOWLIST in x} != {HELPER}:
            raise RuntimeError('Unexpected manifest packages match the exact allowlist.')
        return True

    def begin(self):
        if not self.prepare():
            self.status('waiting_for_existing_test', 'A Step Lab test is already active; no install or second instrumentation is attempted.')
            return
        self.run_id = uuid.uuid4().hex
        self.segment_elapsed, self.previous_progress = 0.0, -1.0
        self.segment_duration = max(1.0, self.args.duration-self.completed)
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        self.prefix = lab.REPORTS/f'mumu-sensor-{stamp}-{self.run_id[:8]}'
        before = self.adb('shell', 'dumpsys', 'sensorservice')
        self.prefix.with_suffix('.before.txt').write_text(before, encoding='utf-8')
        if mode(before) != 'NORMAL' or self.active():
            self.run_id = None
            raise RuntimeError('Preflight changed; no instrumentation was started.')
        self.adb('shell', 'dumpsys', 'sensorservice', 'data_injection', ALLOWLIST)
        entered = self.adb('shell', 'dumpsys', 'sensorservice')
        self.prefix.with_suffix('.entered.txt').write_text(entered, encoding='utf-8')
        found = re.search(r'^Mode[ \t]*:[ \t]*DATA_INJECTION[ \t]*:[ \t]*([^\r\n]*)', entered, re.M)
        if not found or found[1].strip() != ALLOWLIST:
            raise RuntimeError('The exact test allowlist was not applied.')
        command = [lab.executable('adb'), '-s', self.serial, 'shell', 'am', 'instrument', '-w', '-r',
            '--no-hidden-api-checks', '-e', 'run_id', self.run_id,
            '-e', 'duration', str(self.segment_duration), '-e', 'cadence', str(self.args.cadence),
            '-e', 'cadence_variation', str(self.args.cadence_variation),
            '-e', 'amplitude', str(self.args.amplitude), '-e', 'rate', str(self.args.rate),
            '-e', 'show_ui', str(self.args.show_ui).lower(), '-e', 'progress', 'true',
            HELPER+'/.InputInstrumentation']
        self.output = self.prefix.with_suffix('.instrumentation.txt').open('wb')
        self.process = subprocess.Popen(command, stdout=self.output, stderr=subprocess.STDOUT,
            env=lab.env(), creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        self.started = time.monotonic()
        self.status('running', 'Only the independent Step Lab package is instrumented.')

    def finish_segment(self, summary):
        self.close_client()
        if summary:
            if summary.get('package') != HELPER or summary.get('run_id') != self.run_id:
                raise RuntimeError('Current summary identity mismatch.')
            # The next instrumentation truncates these remote files. Keep the
            # evidence even if the emulator subsequently refuses NORMAL.
            self.prefix.with_suffix('.jsonl').write_text(self.adb('shell', 'run-as', HELPER,
                'cat', 'files/sensor-selftest.jsonl', check=False), encoding='utf-8')
            summary.update(host_verified_restored_normal=False, host_allowlist=ALLOWLIST)
            self.prefix.with_suffix('.summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        try:
            self.restore()
        except RuntimeError as error:
            if summary:
                summary['host_restore_error'] = str(error)
                self.prefix.with_suffix('.summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
            raise
        stopped = False
        if summary:
            summary.update(host_verified_restored_normal=True, host_allowlist=ALLOWLIST)
            self.prefix.with_suffix('.summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
            elapsed = float(summary.get('actual_injection_elapsed_ns', 0))/1e9
            self.completed += min(self.segment_duration, max(self.segment_elapsed, elapsed))
            if summary.get('error') or not summary.get('accepted'):
                raise RuntimeError('The helper reported an injection error; see the saved summary.')
            stopped = bool(summary.get('stopped'))
        else:
            self.completed += min(self.segment_duration, self.segment_elapsed)
            self.prefix.with_suffix('.interrupted.json').write_text(json.dumps(dict(
                run_id=self.run_id, credited_seconds=self.segment_elapsed,
                reason='Remote test ended/rebooted without a matching summary; only measured progress was credited.'), indent=2), encoding='utf-8')
        self.run_id, self.segment_elapsed = None, 0.0
        return stopped

    def request_stop(self):
        if not self.ready():
            self.status('stop_unconfirmed_offline', 'Device offline; remote stop and NORMAL restoration cannot be confirmed.')
            return False
        if self.run_id is None and not self.args.stop and self.active():
            self.status('existing_test_left_running', 'This supervisor did not start the active test; it was not stopped.')
            return False
        deadline = time.monotonic()+20
        while self.active():
            self.adb('shell', 'run-as', HELPER, 'touch', 'files/stop.flag')
            if time.monotonic() > deadline:
                self.status('stop_unconfirmed', 'Helper remains active; emulator and production app were not restarted.')
                return False
            time.sleep(0.5)
        self.close_client(); self.restore()
        return True

    def tick(self):
        if self.run_id is None:
            if self.attach_pending:
                self.attach()
                return None
            if self.attached:
                raise RuntimeError('The attached remote test ended; another instrumentation was not started.')
            self.begin()
            return None
        progress = self.remote_json('sensor-progress.json')
        if progress and progress.get('run_id') == self.run_id:
            self.segment_elapsed = max(self.segment_elapsed, float(progress.get('elapsed_seconds', 0)))
            if not self.args.no_progress and self.segment_elapsed != self.previous_progress:
                print(json.dumps(progress, ensure_ascii=False), flush=True)
                self.previous_progress = self.segment_elapsed
            self.status('running', 'Monitoring this run after reconnect; no duplicate instrumentation is started.')
        if self.active():
            if time.monotonic()-self.started > self.segment_duration+60:
                raise RuntimeError('Helper exceeded its bounded duration; graceful cleanup will be requested.')
            return None
        if self.process is not None and self.process.poll() is None and time.monotonic()-self.started < 10:
            return None
        summary = self.remote_json('sensor-selftest-summary.json')
        if not summary or summary.get('run_id') != self.run_id:
            summary = None
        stopped = self.finish_segment(summary)
        if self.attached and not stopped:
            if not summary:
                raise RuntimeError('The attached test ended without its matching summary; no replacement was started.')
            return 'attached_finished'
        return stopped

    def run(self):
        if self.args.stop:
            result = self.request_stop()
            self.status('stopped' if result else 'stop_failed')
            return 0 if result else 1
        with socket.socket() as lock:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                lock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            lock.bind(('127.0.0.1', 15582)); lock.listen(1)
            try:
                while self.completed+0.01 < self.args.duration:
                    if not self.ready():
                        self.status('waiting_for_device', 'Waiting for the existing MuMu device to boot/reconnect; no automatic launch.')
                        time.sleep(2); continue
                    try:
                        stopped = self.tick()
                    except (RuntimeError, OSError, subprocess.TimeoutExpired):
                        if not self.ready():
                            self.status('waiting_for_device', 'ADB transport was lost; remote state will be checked before starting any new test.')
                            time.sleep(2); continue
                        raise
                    if stopped == 'attached_finished':
                        self.status('finished', 'Attached test completed; its summary was saved and NORMAL restored.')
                        return 0
                    if stopped:
                        self.status('stopped', 'Graceful stop confirmed and NORMAL restored.')
                        return 0
                    time.sleep(1)
                self.status('finished', 'Measured requested duration completed and NORMAL restored.')
                return 0
            except KeyboardInterrupt:
                result = self.request_stop()
                self.status('stopped' if result else 'stop_failed')
                return 0 if result else 1
            except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
                self.status('failed', str(error))
                try:
                    if self.run_id and self.ready():
                        self.request_stop()
                except Exception as cleanup:
                    print('Cleanup not confirmed: '+str(cleanup), flush=True)
                raise
            finally:
                if self.output is not None and (self.process is None or self.process.poll() is not None):
                    self.output.close()


def main(argv=None):
    args = arguments(argv)
    if not args.stop:
        from mumu_start_position import load_profile, route_reviewed
        if not route_reviewed(load_profile()):
            raise RuntimeError('Sanitized source snapshot: restore and review private local configuration before sensor input.')
    return StepSupervisor(args).run()


if __name__ == '__main__':
    raise SystemExit(main())
