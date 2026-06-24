import sys
from datetime import datetime, timedelta, timezone
from nlp.event_extractor import parse_datetime_safe, compute_stale_flag, extract_source_url, STALE_THRESHOLD_MINUTES

def test_all():
    now = datetime.now(timezone.utc)
    # Test datetime parser
    assert parse_datetime_safe('2023-10-10 10:10:10') is not None
    assert parse_datetime_safe('2023-10-10T10:10:10Z') is not None
    assert parse_datetime_safe(None) is None
    
    # Test stale flag
    # Current time
    dt_now = now
    assert compute_stale_flag(dt_now, now) == 0
    # Yesterday
    dt_yesterday = now - timedelta(days=1)
    assert compute_stale_flag(dt_yesterday, now) == 1
    # Missing
    assert compute_stale_flag(None, now) == 1
    
    # Test url
    assert extract_source_url('{"url": "http://test"}') == "http://test"
    assert extract_source_url('{"source_url": "http://test2"}') == "http://test2"
    assert extract_source_url('{"link": "http://test3"}') == "http://test3"
    assert extract_source_url('{"other": "test"}') == ""
    assert extract_source_url(None) == ""
    assert extract_source_url('invalid json') == ""

    print("All tests passed")

test_all()
