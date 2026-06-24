import sys, os
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..')) 
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import threading
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class CircuitBreaker:
    def __init__(self, failure_threshold=5, recovery_timeout_minutes=5, half_open_attempts=2):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = timedelta(minutes=recovery_timeout_minutes)
        self.half_open_attempts = half_open_attempts
        self.lock = threading.Lock()
        self.failures = {}
        self.last_failure_time = {}
        self.half_open_counters = {}

    def allow(self, provider_name):
        with self.lock:
            if provider_name not in self.failures:
                return True
                
            if self.failures[provider_name] >= self.failure_threshold:
                if datetime.now() - self.last_failure_time[provider_name] > self.recovery_timeout:
                    attempts = self.half_open_counters.get(provider_name, 0)
                    if attempts < self.half_open_attempts:
                        self.half_open_counters[provider_name] = attempts + 1
                        logger.info(f"[CIRCUIT_HALF_OPEN] Provider {provider_name} in half-open state. Attempt {attempts + 1}/{self.half_open_attempts}.")
                        return True
                    return False
                return False
            return True

    def success(self, provider_name):
        with self.lock:
            self.failures[provider_name] = 0
            self.half_open_counters[provider_name] = 0

    def failure(self, provider_name):
        with self.lock:
            self.failures[provider_name] = self.failures.get(provider_name, 0) + 1
            self.last_failure_time[provider_name] = datetime.now()
            self.half_open_counters[provider_name] = 0
