"""Contract tests for the decision dashboard template."""
from pathlib import Path

import stock_data_fetcher as sdf


TEMPLATE = (Path(__file__).parents[1] / "references" /
            "output-format-template.md").read_text()


def test_dashboard_exposes_hard_gate_status():
    assert "**Hard Gates**: {hard_gates_status}" in TEMPLATE
    assert "fired({buy_gates})" in TEMPLATE
    assert "otherwise render `none`" in TEMPLATE


def test_hard_gate_status_preserves_script_gate_text():
    gates = ["rr_ratio=1.2 < 1.5: risk/reward not justified",
             "limit_up: limit up sealed, buy not executable today (T+1)"]
    rendered = f"fired({'; '.join(gates)})"

    assert rendered.startswith("fired(")
    assert all(gate in rendered for gate in gates)
    assert "none" not in rendered


def test_template_exposes_decision_state_contract():
    assert "| Decision State | {decision_state_en}" in TEMPLATE
    assert "{decision_confidence}" in TEMPLATE
    assert "{decision_horizon}" in TEMPLATE
    # Every allowed state must be documented in the template mapping table.
    for state in sdf.DECISION_STATES:
        assert state in TEMPLATE, f"decision state {state} missing from template"


def test_template_exposes_data_quality_and_source_chain():
    assert "{data_quality_level}" in TEMPLATE
    assert "{fallback_used}" in TEMPLATE
    assert "{source_chain}" in TEMPLATE


def test_template_exposes_market_rules_and_currency():
    assert "{market_rules_en}" in TEMPLATE
    assert "{currency}" in TEMPLATE


def test_template_exposes_decision_evidence_and_disclaimer():
    for placeholder in ("{supporting_evidence}", "{opposing_evidence}",
                        "{key_risks}", "{suggested_action_range}",
                        "{position_size_suggestion}",
                        "{re_evaluation_triggers}"):
        assert placeholder in TEMPLATE, f"{placeholder} missing from template"
    assert sdf.DECISION_DISCLAIMER in TEMPLATE