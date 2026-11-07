"""Normal startup retains provider data; synthetic demo creation is explicit."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select

from market_analysis.api.app import app
from market_analysis.persistence.market_data import dataset_revisions
from market_analysis.persistence.runs import metadata


@pytest.mark.parametrize("setting,seeded", [(None, False), ("0", False), ("1", True)])
def test_api_only_creates_demo_revisions_when_explicitly_enabled(
    tmp_path, monkeypatch, setting, seeded
):
    url = f"sqlite:///{tmp_path / 'startup.sqlite'}"
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", url)
    monkeypatch.setenv("STREAM_ANALYSIS_DATA_ROOT", str(tmp_path / "data"))
    if setting is None:
        monkeypatch.delenv("STREAM_ANALYSIS_SEED_REPLAY_DEMO", raising=False)
    else:
        monkeypatch.setenv("STREAM_ANALYSIS_SEED_REPLAY_DEMO", setting)
    engine = create_engine(url)
    metadata.create_all(engine)
    try:
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
        with engine.connect() as connection:
            count = connection.scalar(select(func.count()).select_from(dataset_revisions))
            assert (count > 0) == seeded
    finally:
        engine.dispose()


@pytest.mark.parametrize("demo", [False, True])
def test_start_wrapper_consumes_demo_flag_and_preserves_compose_arguments(tmp_path, demo):
    stub = tmp_path / "docker"
    stub.write_text(
        f"#!{sys.executable}\n"
        "import json,os,sys\n"
        "print(json.dumps({\n"
        " 'args':sys.argv[1:],\n"
        " 'demo':os.getenv('STREAM_ANALYSIS_SEED_REPLAY_DEMO','0'),\n"
        " 'lineage':bool(os.getenv('STREAM_ANALYSIS_GIT_COMMIT'))}))\n"
    )
    stub.chmod(0o755)
    environment = dict(os.environ)
    environment["PATH"] = str(tmp_path) + os.pathsep + environment["PATH"]
    environment.pop("STREAM_ANALYSIS_SEED_REPLAY_DEMO", None)
    arguments = (
        ["sh", "scripts/start-with-lineage.sh"]
        + (["--seed-demo"] if demo else [])
        + ["--env-file", "fixture.env"]
    )
    result = subprocess.run(
        arguments,
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    output = json.loads(result.stdout)
    assert output["args"] == ["compose", "--env-file", "fixture.env", "up", "--build"]
    assert output["demo"] == ("1" if demo else "0")
    assert output["lineage"]
