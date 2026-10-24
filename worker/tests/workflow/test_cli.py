import json

from tandem_worker.cli import main


def test_manual_fixture_cli_seed_pause_answer_reconcile(tmp_path, monkeypatch, capsys):
    def call(*arguments):
        monkeypatch.setattr("sys.argv", ["tandem-worker", *arguments, "--state-dir", str(tmp_path)])
        main()
        return json.loads(capsys.readouterr().out)
    job = call("seed", "clarification-restart")["job_id"]
    assert call("run-one", job, "--scenario", "clarification-restart")["outcome"] == "WAITING_FOR_INPUT"
    question = call("questions", "--owner", "p1")[0]
    assert call("answer", question["id"], "--owner", "p1", "--response", "yes")["answer_ref"] == question["id"]
    assert call("reconcile", "--scenario", "clarification-restart")[0]["outcome"] == "AWAITING_REVIEW"
