from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from tandem_worker.contracts import ContractError
from tandem_worker.graphs.intake import run_intake


def test_private_intake_reports_generic_activity_and_clears_it_on_success():
    backend, claim, model = Mock(), object(), object()
    backend.load_intake.return_value = SimpleNamespace(owner='person-1')
    with patch('tandem_worker.graphs.intake._run_intake', return_value='ASKED') as execute:
        assert run_intake(backend, claim, model) == 'ASKED'
        execute.assert_called_once_with(backend, claim, model, backend.load_intake.return_value)
    assert [(c.args[1], c.args[2]) for c in backend.set_agent_activity.call_args_list] == [
        ('person-1', 'THINKING'), ('person-1', 'IDLE')]


def test_failed_intake_clears_activity_without_hiding_failure():
    backend = Mock()
    backend.load_intake.return_value = SimpleNamespace(owner='person-1')
    with patch('tandem_worker.graphs.intake._run_intake', side_effect=RuntimeError('provider failed')):
        with pytest.raises(RuntimeError, match='provider failed'):
            run_intake(backend, object(), object())
    assert backend.set_agent_activity.call_args.args[2] == 'IDLE'


def test_activity_outage_does_not_block_intake_but_a_lost_lease_does():
    backend = Mock()
    backend.load_intake.return_value = SimpleNamespace(owner='person-1')
    backend.set_agent_activity.side_effect = RuntimeError('telemetry unavailable')
    with patch('tandem_worker.graphs.intake._run_intake', return_value='ASKED'):
        assert run_intake(backend, object(), object()) == 'ASKED'
    backend.set_agent_activity.side_effect = ContractError('LEASE_LOST')
    with patch('tandem_worker.graphs.intake._run_intake') as execute:
        with pytest.raises(ContractError):
            run_intake(backend, object(), object())
        execute.assert_not_called()
