import pytest
import jsonschema
from jsonschema import validate, ValidationError
import sys
import os

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__)) 
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from ai_trader import VETO_SCHEMA
from nlp.event_extractor import EVENT_CARD_SCHEMA

def test_veto_schema_valid():
    valid_data = {
        "action": "hold",
        "reason": "High volatility",
        "confidence": 0.8
    }
    # Should not raise exception
    validate(instance=valid_data, schema=VETO_SCHEMA)

def test_veto_schema_invalid_action():
    invalid_data = {
        "action": "buy", # 'buy' is not in enum, must be 'confirm'
        "reason": "Good",
        "confidence": 0.9
    }
    with pytest.raises(ValidationError):
        validate(instance=invalid_data, schema=VETO_SCHEMA)

def test_event_card_schema_valid():
    valid_data = {
        "symbol": "600519",
        "event_type": "Earnings",
        "polarity": "positive",
        "summary": "Good earnings",
        "key_facts": ["Revenue up 10%"],
        "confidence": 0.95,
        "novelty": 0.8,
        "risk_flags": "none"
    }
    validate(instance=valid_data, schema=EVENT_CARD_SCHEMA)

def test_event_card_schema_missing_fields():
    invalid_data = {
        "symbol": "600519",
        "event_type": "Earnings",
        "polarity": "positive",
        "summary": "Good earnings",
        "key_facts": ["Revenue up 10%"]
        # Missing confidence, novelty, risk_flags
    }
    with pytest.raises(ValidationError):
        validate(instance=invalid_data, schema=EVENT_CARD_SCHEMA)

if __name__ == "__main__":
    pytest.main(["-v", __file__])
