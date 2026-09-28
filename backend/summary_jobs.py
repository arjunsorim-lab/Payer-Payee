"""Bounded, deduplicated work queue for expensive member summaries."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from threading import Lock


class SummaryJobs:
    def __init__(self, max_entries=128, workers=2):
        self.max_entries = max_entries
        self.jobs = OrderedDict()
        self.lock = Lock()
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="member-summary")

    def request(self, key, calculate):
        with self.lock:
            future = self.jobs.get(key)
            if future is None:
                # Never evict running work: that would allow duplicate requests
                # to start the same calculation and create an unbounded queue.
                while len(self.jobs) >= self.max_entries:
                    completed = next((k for k, v in self.jobs.items() if v.done()), None)
                    if completed is None:
                        return {"status": "busy", "retry_after_ms": 1000}, 202
                    del self.jobs[completed]
                future = self.executor.submit(calculate)
                self.jobs[key] = future
            self.jobs.move_to_end(key)
            if not future.done():
                return {"status": "calculating", "retry_after_ms": 1000}, 202
            try:
                return {"status": "ready", "supportedMoneySummary": future.result()}, 200
            except Exception:
                del self.jobs[key]
                raise


member_summary_jobs = SummaryJobs()
