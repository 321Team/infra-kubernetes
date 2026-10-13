import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / 'engine_stop.py'
spec = importlib.util.spec_from_file_location('engine_stop', SOURCE)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


class GuardTests(unittest.TestCase):
    def test_window_rejects_naive_expired_and_short_recovery(self):
        for args in [('2026-10-14T04:00:00', '2026-10-14T04:05:00+09:00', '2026-10-14T04:30:00+09:00'),
                     ('2026-10-14T04:00:00+09:00', '2026-10-14T04:05:00+09:00', '2026-10-14T04:06:00+09:00')]:
            with self.assertRaises(ValueError):
                m.window(*args, m.stamp('2026-10-14T04:02:00+09:00'))
        with self.assertRaises(ValueError):
            m.window('2026-10-14T04:00:00+09:00', '2026-10-14T04:05:00+09:00', '2026-10-14T04:30:00+09:00', m.stamp('2026-10-14T04:06:00+09:00'))

    def test_replaced_identity_after_pin_refuses_and_closes(self):
        with mock.patch.object(m, 'inspect_identity', side_effect=['S', ValueError('PID start changed')]), mock.patch.object(m.os, 'pidfd_open', return_value=42, create=True), mock.patch.object(m.os, 'close') as close:
            with self.assertRaisesRegex(ValueError, 'PID start'):
                m.pin({'pid': 123})
            close.assert_called_once_with(42)

    def test_exited_original_does_not_resume_replacement(self):
        with mock.patch.object(m, 'exited', return_value=True), mock.patch.object(m.signal, 'pidfd_send_signal', create=True) as send:
            events=[]; m.resume(42, events.append)
            send.assert_not_called()
            self.assertEqual(events, ['ORIGINAL_EXITED_NO_CONT'])

    def test_log_failure_after_stop_still_resumes_same_descriptor(self):
        def log(event):
            if event == 'STOP_SENT': raise OSError('disk full')
        with mock.patch.object(m, 'exited', return_value=False), mock.patch.object(m.signal, 'SIGSTOP', 19, create=True), mock.patch.object(m.signal, 'SIGCONT', 18, create=True), mock.patch.object(m.signal, 'pidfd_send_signal', create=True) as send:
            with self.assertRaises(OSError): m.hold(42, Path('unused'), log, .1)
            self.assertEqual(send.call_args_list, [mock.call(42,19),mock.call(42,18)])

    def test_already_stopped_is_not_a_new_fault(self):
        with mock.patch.object(m, 'inspect_identity', return_value='T'), mock.patch.object(m, 'exited', return_value=False), mock.patch.object(m.os, 'pidfd_open', return_value=42, create=True), mock.patch.object(m.os, 'close'):
            with self.assertRaisesRegex(ValueError, 'already stopped'): m.pin({'pid': 123})


@unittest.skipUnless(sys.platform == 'linux' and hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'), 'Linux pidfd integration only')
class LinuxProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.child = subprocess.Popen([sys.executable, '-I', '-c', 'import time; time.sleep(30)'])
        self.fd = os.pidfd_open(self.child.pid)
    def tearDown(self):
        self.child.kill(); self.child.wait(timeout=3); os.close(self.fd); self.temp.cleanup()
    def state(self):
        text=Path('/proc',str(self.child.pid),'stat').read_text()
        return text[text.rfind(')')+2:].split()[0]
    def test_deadline_resumes_real_stopped_child(self):
        events=[]
        def log(event):
            events.append(event)
            if event=='STOP_SENT':
                deadline=time.monotonic()+1
                while self.state()!='T' and time.monotonic()<deadline: time.sleep(.01)
                self.assertEqual(self.state(),'T')
        result=m.hold(self.fd,self.root/'ABORT',log,.3)
        self.assertEqual(result,'DEADLINE'); self.assertIn('CONT_ORIGINAL_ONLY',events)
        time.sleep(.05); self.assertNotEqual(self.state(),'T')
    def test_abort_restores_before_hold_deadline(self):
        def abort(): time.sleep(.15); (self.root/'ABORT').touch()
        worker=threading.Thread(target=abort); worker.start()
        t=time.monotonic(); result=m.hold(self.fd,self.root/'ABORT',lambda _:None,3); worker.join()
        self.assertEqual(result,'ABORT'); self.assertLess(time.monotonic()-t,1.5)
        time.sleep(.05); self.assertNotEqual(self.state(),'T')
    def test_original_exit_gets_no_cont_or_second_stop(self):
        def terminate(): time.sleep(.15); self.child.kill()
        worker=threading.Thread(target=terminate); worker.start(); events=[]
        result=m.hold(self.fd,self.root/'ABORT',events.append,3); worker.join()
        self.assertEqual(result,'ORIGINAL_EXIT'); self.assertIn('ORIGINAL_EXITED_NO_CONT',events)
        self.assertEqual(events.count('STOP_SENT'),1); self.assertNotIn('CONT_ORIGINAL_ONLY',events)
    def test_interrupt_restores_real_process(self):
        def log(event):
            if event=='STOP_SENT': raise InterruptedError('simulated HUP handler')
        with self.assertRaises(InterruptedError): m.hold(self.fd,self.root/'ABORT',log,3)
        time.sleep(.05); self.assertNotEqual(self.state(),'T')


if __name__ == '__main__': unittest.main()
