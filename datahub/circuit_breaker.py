import sys, os
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..')) 
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import threading
from datetime import datetime, timedelta

class CircuitBreaker:
    def __init__(self, failure_threshold=5, recovery_timeout_minutes=5):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = timedelta(minutes=recovery_timeout_minutes)
        self.lock = threading.Lock()
        self.failures = {}
        self.last_failure_time = {}

    def allow(self, provider_name):
        with self.lock:
            if provider_name not in self.failures:
                return True
                
            if self.failures[provider_name] >= self.failure_threshold:
                if datetime.now() - self.last_failure_time[provider_name] > self.recovery_timeout:
                    return True
                return False
            return True

    def success(self, provider_name):
        with self.lock:
            self.failures[provider_name] = 0

    def failure(self, provider_name):
        with self.lock:
            self.failures[provider_name] = self.failures.get(provider_name, 0) + 1
            self.last_failure_time[provider_name] = datetime.now()
