"""Chargement des deux Lambda pour les tests."""

import importlib.util
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent


def _charger(nom, chemin):
    spec = importlib.util.spec_from_file_location(nom, RACINE / chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def lambda_a(monkeypatch):
    monkeypatch.setenv("QUEUE_URL", "http://localhost:4566/000000000000/commandes")
    return _charger("lambda_a_handler", "src/lambda_a_http/handler.py")


@pytest.fixture
def lambda_b(monkeypatch):
    monkeypatch.setenv("TABLE_NAME", "Commandes")
    return _charger("lambda_b_handler", "src/lambda_b_sqs/handler.py")