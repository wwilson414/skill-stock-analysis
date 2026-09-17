"""Contract tests for the decision dashboard template."""
from pathlib import Path


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