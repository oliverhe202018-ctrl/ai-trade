import pytest
import json
import requests
from unittest.mock import patch, MagicMock

import sys
import os
PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from ai_trader import call_llm_veto

@pytest.fixture
def dummy_data():
    rule_signal = {"action": "buy"}
    events_json = json.dumps([{"id": 1, "stale": False}])
    return rule_signal, events_json

class MockResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code != 200:
            raise requests.exceptions.HTTPError("HTTP Error")

def test_requests_timeout(dummy_data, caplog):
    rule_signal, events_json = dummy_data
    with patch('requests.post', side_effect=requests.exceptions.Timeout("Timeout")):
        result = call_llm_veto(rule_signal, events_json)
        assert result == "veto"
        assert "[LLM_FAILSAFE]" in caplog.text

def test_connection_error(dummy_data, caplog):
    rule_signal, events_json = dummy_data
    with patch('requests.post', side_effect=requests.exceptions.ConnectionError("Connection Error")):
        result = call_llm_veto(rule_signal, events_json)
        assert result == "veto"
        assert "[LLM_FAILSAFE]" in caplog.text

def test_invalid_json(dummy_data, caplog):
    rule_signal, events_json = dummy_data
    with patch('requests.post', return_value=MockResponse({'response': '{invalid json}'})):
        result = call_llm_veto(rule_signal, events_json)
        assert result == "veto"
        assert "[LLM_FAILSAFE]" in caplog.text

def test_empty_json(dummy_data, caplog):
    rule_signal, events_json = dummy_data
    with patch('requests.post', return_value=MockResponse({'response': '{}'})):
        result = call_llm_veto(rule_signal, events_json)
        assert result == "veto"
        # Schema validation fail -> veto

def test_action_is_integer(dummy_data, caplog):
    rule_signal, events_json = dummy_data
    response_payload = {
        "action": 123,
        "reason": "Test reason",
        "confidence": 0.9
    }
    with patch('requests.post', return_value=MockResponse({'response': json.dumps(response_payload)})):
        result = call_llm_veto(rule_signal, events_json)
        assert result == "veto"
        # Validation error for schema (action should be string according to enum) or explicit type check
        # Either SCHEMA_VALIDATION_FAIL or LLM_FAILSAFE

def test_action_is_buy(dummy_data, caplog):
    rule_signal, events_json = dummy_data
    response_payload = {
        "action": "BUY",
        "reason": "Test reason",
        "confidence": 0.9
    }
    with patch('requests.post', return_value=MockResponse({'response': json.dumps(response_payload)})):
        result = call_llm_veto(rule_signal, events_json)
        assert result == "veto"
        # Since "buy" is not in enum ["confirm", "veto", "reduce"], schema validates throws error

def test_action_is_confirm(dummy_data):
    rule_signal, events_json = dummy_data
    response_payload = {
        "action": "confirm",
        "reason": "Looking good",
        "confidence": 0.9
    }
    with patch('requests.post', return_value=MockResponse({'response': json.dumps(response_payload)})):
        result = call_llm_veto(rule_signal, events_json)
        assert result == "confirm"

def test_action_is_veto(dummy_data):
    rule_signal, events_json = dummy_data
    response_payload = {
        "action": "veto",
        "reason": "Too risky",
        "confidence": 0.9
    }
    with patch('requests.post', return_value=MockResponse({'response': json.dumps(response_payload)})):
        result = call_llm_veto(rule_signal, events_json)
        assert result == "veto"

def test_action_is_reduce(dummy_data):
    rule_signal, events_json = dummy_data
    response_payload = {
        "action": "reduce",
        "reason": "Partial profit",
        "confidence": 0.9
    }
    with patch('requests.post', return_value=MockResponse({'response': json.dumps(response_payload)})):
        result = call_llm_veto(rule_signal, events_json)
        assert result == "reduce"
