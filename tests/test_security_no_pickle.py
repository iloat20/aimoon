"""Tests for security: no pickle/joblib serialization in ML models."""

from __future__ import annotations

import pathlib


class TestNoPickleSerialization:
    """Verify that ML model serialization does not use pickle."""

    def should_not_find_joblib_dump_in_ensemble(self) -> None:
        """ensemble.py should not contain joblib.dump calls."""
        src = pathlib.Path("src/aimoon/ml/ensemble.py").read_text(encoding="utf-8")
        assert "joblib.dump" not in src, "Found joblib.dump in ensemble.py"

    def should_not_find_joblib_load_in_ensemble(self) -> None:
        """ensemble.py should not contain joblib.load calls."""
        src = pathlib.Path("src/aimoon/ml/ensemble.py").read_text(encoding="utf-8")
        assert "joblib.load" not in src, "Found joblib.load in ensemble.py"

    def should_not_find_joblib_dump_in_trainer(self) -> None:
        """trainer.py should not contain joblib.dump calls."""
        src = pathlib.Path("src/aimoon/ml/trainer.py").read_text(encoding="utf-8")
        assert "joblib.dump" not in src, "Found joblib.dump in trainer.py"

    def should_not_find_joblib_load_in_trainer(self) -> None:
        """trainer.py should not contain joblib.load calls."""
        src = pathlib.Path("src/aimoon/ml/trainer.py").read_text(encoding="utf-8")
        assert "joblib.load" not in src, "Found joblib.load in trainer.py"

    def should_use_json_for_elasticnet(self) -> None:
        """ElasticNet model save/load should use .json extension."""
        src = pathlib.Path("src/aimoon/ml/ensemble.py").read_text(encoding="utf-8")
        assert "model.elasticnet.json" in src
        assert "model.elasticnet.joblib" not in src
