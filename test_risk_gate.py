import pytest
import sys
import os
import json
from unittest.mock import patch, MagicMock

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from ai_trader import final_risk_gate

@pytest.fixture
def mock_portfolio():
    return {
        "cash": 100000.0,
        "positions": {
            "600519": {"shares": 10, "avg_price": 1500.0}
        },
        "daily_loss_pct": -0.01
    }

@pytest.fixture
def mock_params():
    return {
        "max_single_pct": 0.50,
        "stop_loss_pct": -0.08
    }

def test_risk_gate_veto_passthrough():
    action = final_risk_gate("600519", "veto", {"price": 100}, events=None, portfolio={"cash": 100})
    assert action == "veto"

def test_risk_gate_reduce_passthrough():
    action = final_risk_gate("600519", "reduce", {"price": 100}, events=None, portfolio={"cash": 100})
    assert action == "reduce"

@patch('core.risk_manager._load_hyperparams', return_value={"max_single_pct": 0.50, "stop_loss_pct": -0.08})
@patch('core.trading_state.get_trading_state', return_value='active')
def test_risk_gate_confirm_price_invalid(mock_get_state, mock_load_params, mock_portfolio):
    # price <= 0
    action = final_risk_gate("600519", "confirm", {"price": 0}, events=None, portfolio=mock_portfolio)
    assert action == "veto"
    
    action = final_risk_gate("600519", "confirm", {"price": -10}, events=None, portfolio=mock_portfolio)
    assert action == "veto"

@patch('core.risk_manager._load_hyperparams', return_value={"max_single_pct": 0.50, "stop_loss_pct": -0.08})
@patch('core.trading_state.get_trading_state', return_value='active')
def test_risk_gate_confirm_stale_event(mock_get_state, mock_load_params, mock_portfolio):
    events_json = json.dumps([{"id": 1, "stale": True}])
    action = final_risk_gate("600519", "confirm", {"price": 100}, events=events_json, portfolio=mock_portfolio)
    assert action == "veto"

@patch('core.risk_manager._load_hyperparams', return_value={"max_single_pct": 0.50, "stop_loss_pct": -0.08})
@patch('core.trading_state.get_trading_state', return_value='active')
def test_risk_gate_confirm_valid(mock_get_state, mock_load_params, mock_portfolio):
    events_json = json.dumps([{"id": 1, "stale": False}])
    action = final_risk_gate("600519", "confirm", {"price": 100}, events=events_json, portfolio=mock_portfolio)
    assert action == "confirm"

@patch('core.risk_manager._load_hyperparams', return_value={"max_single_pct": 0.50, "stop_loss_pct": -0.08})
@patch('core.trading_state.get_trading_state', return_value='active')
def test_risk_gate_missing_portfolio(mock_get_state, mock_load_params):
    action = final_risk_gate("600519", "confirm", {"price": 100}, events=None, portfolio=None)
    assert action == "veto"
