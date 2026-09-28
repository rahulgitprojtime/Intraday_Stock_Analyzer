import threading

from src.utils.ratelimit import RateLimiter


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.lock = threading.Lock()

    def now(self):
        with self.lock:
            return self.t

    def sleep(self, s):
        with self.lock:
            self.t += s


def test_spaces_calls_to_the_rate():
    c = FakeClock()
    rl = RateLimiter(per_second=4, clock=c.now, sleep=c.sleep)
    for _ in range(9):
        rl.acquire()
    assert c.t == 2.0                        # 9 calls at 4/s: first free, then 0.25 s apart


def test_thread_safe_total_rate():
    c = FakeClock()
    rl = RateLimiter(per_second=10, clock=c.now, sleep=c.sleep)
    threads = [threading.Thread(target=lambda: [rl.acquire() for _ in range(25)])
               for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert c.t >= 9.9 - 1e-9                 # 100 calls at 10/s: 99 gaps of 0.1 s minimum
