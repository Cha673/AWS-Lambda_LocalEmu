"""Tests de la Lambda B (SQS → DynamoDB), avec une fausse table."""

import json

import pytest
from botocore.exceptions import ClientError


class FausseTable:
    """Remplace la table DynamoDB : un simple dictionnaire indexé par id."""

    def __init__(self):
        self.items = {}
        self.erreur = None

    def put_item(self, Item, ConditionExpression=None):
        if self.erreur:
            raise self.erreur
        if ConditionExpression == "attribute_not_exists(id)" and Item["id"] in self.items:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "existe déjà"}},
                "PutItem",
            )
        self.items[Item["id"]] = Item


@pytest.fixture
def table(lambda_b):
    fausse = FausseTable()
    lambda_b._table = fausse
    return fausse


def record(corps, message_id="m1", receptions="1"):
    if not isinstance(corps, str):
        corps = json.dumps(corps)
    return {
        "messageId": message_id,
        "body": corps,
        "attributes": {"ApproximateReceiveCount": receptions},
    }


def commande(id_commande="c1"):
    return {
        "id": id_commande,
        "client": "Alice",
        "produit": "Clavier",
        "quantite": 2,
        "recu_le": "2026-11-16T10:00:00+00:00",
    }


def test_message_valide_est_enregistre(lambda_b, table):
    resultat = lambda_b.handler({"Records": [record(commande())]}, None)

    assert resultat == {"batchItemFailures": []}
    item = table.items["c1"]
    assert item["client"] == "Alice"
    assert item["quantite"] == 2
    assert item["sqs_message_id"] == "m1"
    assert "traite_le" in item


def test_message_recu_deux_fois_ne_cree_pas_de_doublon(lambda_b, table):
    lambda_b.handler({"Records": [record(commande(), "m1")]}, None)
    premier_traitement = table.items["c1"]["traite_le"]

    resultat = lambda_b.handler({"Records": [record(commande(), "m1", receptions="2")]}, None)

    assert resultat == {"batchItemFailures": []}  # pas d'échec : rien à retenter
    assert len(table.items) == 1
    assert table.items["c1"]["traite_le"] == premier_traitement  # item non écrasé


@pytest.mark.parametrize("corps", ["pas du json", '"juste un texte"', "{}"])
def test_message_invalide_est_signale_en_echec(lambda_b, table, corps):
    resultat = lambda_b.handler({"Records": [record(corps, "m-bad")]}, None)

    assert resultat == {"batchItemFailures": [{"itemIdentifier": "m-bad"}]}
    assert table.items == {}


def test_seul_le_message_en_echec_est_retente(lambda_b, table):
    lot = [
        record(commande("c1"), "m1"),
        record("invalide", "m2"),
        record(commande("c3"), "m3"),
    ]
    resultat = lambda_b.handler({"Records": lot}, None)

    assert resultat == {"batchItemFailures": [{"itemIdentifier": "m2"}]}
    assert set(table.items) == {"c1", "c3"}


def test_erreur_dynamodb_est_signalee_en_echec(lambda_b, table):
    table.erreur = ClientError(
        {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "trop de requêtes"}},
        "PutItem",
    )
    resultat = lambda_b.handler({"Records": [record(commande(), "m1")]}, None)

    assert resultat == {"batchItemFailures": [{"itemIdentifier": "m1"}]}


def test_lot_vide(lambda_b, table):
    assert lambda_b.handler({"Records": []}, None) == {"batchItemFailures": []}