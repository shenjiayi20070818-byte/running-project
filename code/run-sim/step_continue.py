"""Continue one existing Step Lab experiment after its prior client has closed."""
import argparse
import datetime
import hashlib
import json
import re
import subprocess
import sys
import time
import uuid

import step_sim as step


def injection_allowlist(text):
    match = re.search(r'^Mode\s*:\s*DATA_INJECTION[ \t]*:[ \t]*([^\r\n]*)', text, re.M)
    return match[1].strip() if match else None


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous-run-id', required=True)
    parser.add_argument('--reuse-existing-mode', action='store_true',
                        help='Explicitly allow an already existing empty/exact DATA_INJECTION mode after NORMAL fails.')
    for name, default in [('duration', 400), ('cadence', 180), ('cadence-variation', 6),
                          ('amplitude', 6), ('rate', 50)]:
        parser.add_argument('--'+name, type=float, default=default)
    parser.add_argument('--show-ui', action='store_true')
    parser.add_argument('--no-progress', action='store_true')
    parser.add_argument('--index', type=int, default=0)
    args = parser.parse_args(argv)
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', args.previous_run_id):
        parser.error('Invalid previous run ID.')
    validated = step.arguments(['--duration', str(args.duration), '--cadence', str(args.cadence),
        '--cadence-variation', str(args.cadence_variation), '--amplitude', str(args.amplitude),
        '--rate', str(args.rate), '--index', str(args.index)])
    args.stop, args.attach_run_id = validated.stop, None
    return args


class ContinuationSupervisor(step.StepSupervisor):
    def __init__(self, args):
        super().__init__(args)
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        self.handoff_file = step.lab.REPORTS / ('step-continuation-'+stamp+'-'+uuid.uuid4().hex[:8]+'.json')
        self.handoff = dict(previous_run_id=args.previous_run_id,
                            reuse_explicitly_authorized=args.reuse_existing_mode,
                            install_attempted=False, permissions_changed=False,
                            requested_seconds=args.duration, final_normal_verified=False)
        self.reusing_mode = False

    def save_handoff(self):
        self.handoff_file.write_text(json.dumps(self.handoff, ensure_ascii=False, indent=2), encoding='utf-8')

    def prepare(self):
        # No writes or new test client while the old instrumentation is active.
        if self.active():
            return False
        previous = self.remote_json('sensor-selftest-summary.json')
        if (not previous or previous.get('package') != step.HELPER
                or previous.get('run_id') != self.args.previous_run_id
                or previous.get('client_closed') is not True):
            raise RuntimeError('Previous matching summary/client_closed=true is required before continuation.')
        self.handoff['previous_summary'] = previous
        target = self.adb('shell', 'dumpsys', 'package', step.lab.APP)
        if not (re.search(r'versionCode=408\b', target)
                and re.search(r'versionName=7\.4\.0\b', target)):
            raise RuntimeError('Original target 7.4.0 / 408 is required.')
        packages = {row.removeprefix('package:') for row in self.adb('shell', 'pm', 'list', 'packages').splitlines()}
        if {pkg for pkg in packages if step.ALLOWLIST in pkg} != {step.HELPER}:
            raise RuntimeError('Unexpected manifest packages match the narrow test allowlist.')
        helper_info = self.adb('shell', 'dumpsys', 'package', step.HELPER)
        if not (re.search(r'versionCode=1\b', helper_info)
                and re.search(r'versionName=1\.0\b', helper_info)):
            raise RuntimeError('Reviewed Step Lab 1.0 / 1 must already be installed.')
        baseline = json.loads((step.lab.WORK/'step-helper-build/artifact.json').read_text(encoding='utf-8'))
        apk = step.lab.BASE.parent/'apks/mumu-step-test.apk'
        digest = hashlib.sha256(apk.read_bytes()).hexdigest()
        if (baseline.get('package') != step.HELPER or baseline.get('sha256') != digest
                or baseline.get('permissions') != []):
            raise RuntimeError('Local reviewed APK digest/permission baseline mismatch.')
        paths = self.adb('shell', 'pm', 'path', step.HELPER).splitlines()
        if len(paths) != 1 or not paths[0].startswith('package:/data/app/'):
            raise RuntimeError('Expected an existing single-APK Step Lab installation.')
        remote = paths[0].removeprefix('package:')
        if not re.fullmatch(r'[A-Za-z0-9_./=~+\-]+', remote):
            raise RuntimeError('Unexpected installed APK path.')
        actual = self.adb('shell', 'sha256sum', remote).split()
        if not actual or actual[0].lower() != digest:
            raise RuntimeError('Installed APK differs from the signed reviewed APK; no install was attempted.')
        # Whole-APK equality includes its exact manifest and v2/v3 signing block.
        self.handoff.update(apk_sha256=digest, installed_apk_identical=True,
                            installed_version='1.0 / 1', signature_baseline='identical signed APK bytes')
        before = self.adb('shell', 'dumpsys', 'sensorservice')
        self.handoff['initial_mode'] = step.mode(before)
        self.handoff['initial_allowlist'] = injection_allowlist(before)
        self.save_handoff()
        try:
            self.restore()  # existing implementation performs two verified attempts
            self.handoff['preflight_normal_verified'] = True
        except RuntimeError as error:
            after = self.adb('shell', 'dumpsys', 'sensorservice')
            allowed = injection_allowlist(after)
            self.handoff.update(preflight_normal_verified=False, preflight_restore_error=str(error),
                                preflight_mode=step.mode(after), preflight_allowlist=allowed)
            self.save_handoff()
            if (not self.args.reuse_existing_mode or self.active()
                    or step.mode(after) != 'DATA_INJECTION' or allowed not in ('', step.ALLOWLIST)):
                raise RuntimeError('NORMAL was not restored; continuation refused without explicit eligible mode reuse.') from error
            self.reusing_mode = True
        self.handoff['reused_existing_abnormal_mode'] = self.reusing_mode
        self.save_handoff()
        return True

    def begin(self):
        if not self.prepare():
            self.status('waiting_for_existing_test', 'Old instrumentation is still active; no continuation was started.')
            return
        self.run_id = uuid.uuid4().hex
        self.segment_elapsed, self.previous_progress = 0.0, -1.0
        self.segment_duration = self.args.duration
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        self.prefix = step.lab.REPORTS/f'mumu-sensor-{stamp}-{self.run_id[:8]}'
        before = self.adb('shell', 'dumpsys', 'sensorservice')
        if self.active():
            self.run_id = None
            raise RuntimeError('An instrumentation appeared during continuation preflight; no second test was started.')
        self.prefix.with_suffix('.before.txt').write_text(before, encoding='utf-8')
        if self.reusing_mode:
            if step.mode(before) != 'DATA_INJECTION' or injection_allowlist(before) not in ('', step.ALLOWLIST):
                self.run_id = None
                raise RuntimeError('Existing injection mode changed; it was not altered or reused.')
        else:
            if step.mode(before) != 'NORMAL':
                self.run_id = None
                raise RuntimeError('NORMAL changed before continuation; no new test was started.')
            self.adb('shell', 'dumpsys', 'sensorservice', 'data_injection', step.ALLOWLIST)
        entered = self.adb('shell', 'dumpsys', 'sensorservice')
        if step.mode(entered) != 'DATA_INJECTION' or injection_allowlist(entered) not in (
                ('', step.ALLOWLIST) if self.reusing_mode else (step.ALLOWLIST,)):
            raise RuntimeError('Continuation injection preflight did not match the expected mode.')
        self.prefix.with_suffix('.entered.txt').write_text(entered, encoding='utf-8')
        command = [step.lab.executable('adb'), '-s', self.serial, 'shell', 'am', 'instrument', '-w', '-r',
            '--no-hidden-api-checks', '-e', 'run_id', self.run_id,
            '-e', 'duration', str(self.segment_duration), '-e', 'cadence', str(self.args.cadence),
            '-e', 'cadence_variation', str(self.args.cadence_variation),
            '-e', 'amplitude', str(self.args.amplitude), '-e', 'rate', str(self.args.rate),
            '-e', 'show_ui', str(self.args.show_ui).lower(), '-e', 'progress', 'true',
            step.HELPER+'/.InputInstrumentation']
        self.output = self.prefix.with_suffix('.instrumentation.txt').open('wb')
        self.process = subprocess.Popen(command, stdout=self.output, stderr=subprocess.STDOUT,
            env=step.lab.env(), creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        self.started = time.monotonic()
        self.attached = True  # use the base monitor's single-segment completion path
        self.handoff.update(continuation_run_id=self.run_id, report_prefix=str(self.prefix),
                            new_client_started=True, mode_changed_for_start=not self.reusing_mode)
        self.save_handoff()
        self.status('running', 'Continuation started from the identical installed Step Lab APK; existing-mode reuse='+str(self.reusing_mode))

    def finish_segment(self, summary):
        self.close_client()
        # Preserve the evidence before attempting a possibly failing HAL reset.
        if not summary or summary.get('package') != step.HELPER or summary.get('run_id') != self.run_id:
            raise RuntimeError('Continuation ended without its matching summary.')
        self.prefix.with_suffix('.jsonl').write_text(self.adb('shell', 'run-as', step.HELPER,
            'cat', 'files/sensor-selftest.jsonl', check=False), encoding='utf-8')
        failure = None
        try:
            self.restore()
            self.handoff['final_normal_verified'] = True
        except RuntimeError as error:
            failure = error
            self.handoff['final_restore_error'] = str(error)
        final = self.adb('shell', 'dumpsys', 'sensorservice')
        self.handoff.update(final_mode=step.mode(final), final_allowlist=injection_allowlist(final))
        summary.update(host_verified_restored_normal=self.handoff['final_normal_verified'],
                       reused_existing_abnormal_mode=self.reusing_mode, host_allowlist=step.ALLOWLIST)
        self.prefix.with_suffix('.summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        self.handoff['continuation_summary'] = summary
        self.save_handoff()
        self.completed += min(self.segment_duration, float(summary.get('actual_injection_elapsed_ns', 0))/1e9)
        self.run_id, self.segment_elapsed = None, 0.0
        if failure:
            raise failure
        if summary.get('error') or not summary.get('accepted') or summary.get('client_closed') is not True:
            raise RuntimeError('Continuation helper reported failure; see its saved summary.')
        return bool(summary.get('stopped'))


if __name__ == '__main__':
    raise SystemExit(ContinuationSupervisor(arguments()).run())
