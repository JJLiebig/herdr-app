import unittest
from report import assess
from run import Desktop
from threading import Event


class ReportTests(unittest.TestCase):
    def test_focus_loss_and_f12_stop_before_any_injection(self):
        desktop = Desktop.__new__(Desktop)
        desktop.abort = Event()
        class User32:
            def GetForegroundWindow(self):
                return 999
            def SendInput(self, *args):
                raise AssertionError('must not reach SendInput')
        desktop.u = User32()
        desktop.pid = lambda hwnd: 42
        with self.assertRaisesRegex(RuntimeError, 'foreground'):
            desktop.chord(123, 42, [65])
        desktop.abort.set()
        with self.assertRaisesRegex(RuntimeError, 'F12'):
            desktop.chord(123, 42, [65])

    def test_issue_85_rejects_cancel_alt_and_trailing_text(self):
        good = [[1, 1, 1, 27, 1, 27, 0], [1, 0, 1, 27, 1, 27, 0],
                [1, 1, 1, 67, 46, 3, 8]]
        def check(records):
            return assess('native', [[27], [17, 67]], dict(records=records, quiet_reached=True))
        self.assertIsNone(check(good))
        self.assertIsNotNone(check([[1, 1, 1, 3, 0, 3, 10]]))
        self.assertIsNotNone(check([good[0], [1, 1, 1, 67, 46, 3, 10]]))
        self.assertIsNotNone(check(good + [[1, 1, 1, 0, 0, 32, 0]]))

    def test_kitty_requires_distinct_events_and_no_trailing_text(self):
        def check(data):
            return assess('kitty5', [[27], [17, 67]], dict(hex=data.hex(), quiet_reached=True))
        self.assertIsNone(check(b'\x1b[27u\x1b[99;5u'))
        self.assertIsNotNone(check(b'\x1b\x03'))
        self.assertIsNotNone(check(b'\x1b[27u\x1b[99;5u '))


if __name__ == '__main__':
    unittest.main()
