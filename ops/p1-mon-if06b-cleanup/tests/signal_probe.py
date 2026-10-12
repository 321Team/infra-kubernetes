"""Child-process probe: delivers signals at chosen lines of the trigger wait. SYNTHETIC.

Run only by test_guardian.py with a timeout, so a deadlock ends the child and
fails the test instead of hanging it. Never run against a live guardian.
"""
import json
import linecache
import os
from pathlib import Path
import signal
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import guardian as g
import supervisor as s


class LegacyTriggers:
    """The pre-fix implementation, kept only to prove this probe detects it."""
    def __init__(self):
        self._event, self._value = threading.Event(), None
        signal.signal(signal.SIGUSR1, lambda _n, _f: self._set('abort'))

    def _set(self, name):
        if self._value is None:
            self._value = name
        self._event.set()

    def wait(self, timeout):
        self._event.wait(timeout)
        return self._value


def inject(code, signame, index=None, text=None):
    """Raise `signame` once: at the index-th line event of `code`, or at a source line."""
    fired, seen = [], [0]

    def local(frame, event, _arg):
        if event == 'line' and not fired:
            here = linecache.getline(frame.f_code.co_filename, frame.f_lineno)
            if seen[0] == index or (text is not None and text in here):
                fired.append(here.strip())
                signal.raise_signal(getattr(signal, signame))
            seen[0] += 1
        return local
    sys.settrace(lambda frame, _event, _arg: local if frame.f_code is code else None)
    return fired


def timed(wait, seconds):
    started = time.monotonic()
    value = wait(seconds)
    sys.settrace(None)
    return value, time.monotonic() - started


def main(mode, signame='SIGUSR1', index='0'):
    out = dict(mode=mode)
    if mode == 'legacy':  # Expected to hang: the handler re-enters the Event's lock.
        fired = inject(threading.Event.wait.__code__, 'SIGUSR1', text='signaled = self._flag')
        out['value'], out['elapsed'] = timed(LegacyTriggers().wait, 0.05)
        out['fired'] = fired
    elif mode == 'wait':  # One signal at each line of the real wait().
        triggers = g.Triggers()
        fired = inject(g.Triggers.wait.__code__, signame, index=int(index))
        # Short: the `return` line is only reached, and injected, after the timeout.
        out['value'], out['elapsed'] = timed(triggers.wait, 0.3)
        out['fired'] = fired
    elif mode == 'nested':  # A second signal at each line of the handler itself.
        triggers = g.Triggers()
        fired = inject(g.Triggers._set.__code__, 'SIGUSR2', index=int(index))
        signal.raise_signal(getattr(signal, signame))
        out['value'], out['elapsed'] = timed(triggers.wait, 5)
        out['fired'] = fired
    elif mode == 'expiry':  # No signal: the wait ends by timeout, then one arrives late.
        triggers = g.Triggers()
        out['value'], out['elapsed'] = timed(triggers.wait, 0.2)
        signal.raise_signal(signal.SIGTERM)
        out['late'] = triggers.wait(5)
    elif mode == 'storm':  # More signals than the pipe holds; first trigger wins.
        triggers = g.Triggers()
        signal.raise_signal(signal.SIGUSR2)
        for count in range(70000):
            signal.raise_signal((signal.SIGUSR1, signal.SIGTERM, signal.SIGINT, signal.SIGUSR2)[count % 4])
        out['value'], out['elapsed'] = timed(triggers.wait, 5)
        triggers.close()
        reused = os.pipe()  # Takes the descriptor numbers that close() released.
        os.set_blocking(reused[0], False)
        for number in (signal.SIGUSR1, signal.SIGUSR2, signal.SIGTERM, signal.SIGINT):
            signal.raise_signal(number)
        try:
            out['written_after_close'] = len(os.read(reused[0], 16))
        except BlockingIOError:
            out['written_after_close'] = 0
        triggers.close()  # Idempotent.
        out['after_close'] = triggers.wait(0.05)
    elif mode == 'broken':  # Descriptors lost underneath: never an exception out of a handler.
        triggers = g.Triggers()
        os.close(triggers._wake)
        signal.raise_signal(signal.SIGUSR1)
        out['value'] = triggers.wait(5)
        blind = g.Triggers()
        os.close(blind._read)
        out['await'] = s._await(time.monotonic() + 5, time.monotonic, blind.wait)
    print(json.dumps(out))


if __name__ == '__main__':
    main(*sys.argv[1:])
