"""Tests de la Lambda A (HTTP → SQS), avec un faux client SQS."""

import base64
import json

import pytest


class FauxSQS:
    """Remplace boto3 : garde en mémoire les messages envoyés."""

    def __init__(self):
        self.messages = []
        self.erreur = None

    def send_message(self, QueueUrl, MessageBody):
        if self.erreur:
            raise self.erreur
        self.messages.append({"QueueUrl": QueueUrl, "MessageBody": MessageBody})
        return {"MessageId": f"msg-{len(self.messages)}"}


@pytest.fixture
def sqs(lambda_a):
    faux = FauxSQS()
    lambda_a._sqs = faux
    return faux


def appeler(lambda_a, corps, base64_encode=False):
    if isinstance(corps, dict):
        corps = json.dumps(corps)
    if base64_encode:
        corps = base64.b64encode(corps.encode()).decode()
    reponse = lambda_a.handler({"body": corps, "isBase64Encoded": base64_encode}, None)
    return reponse["statusCode"], json.loads(reponse["body"])


COMMANDE_VALIDE = {"client": "Alice", "produit": "Clavier", "quantite": 2}


def test_commande_valide_repond_202_et_publie_un_message(lambda_a, sqs):
    code, corps = appeler(lambda_a, COMMANDE_VALIDE)

    assert code == 202
    assert corps["statut"] == "accepte"
    assert len(sqs.messages) == 1
    assert sqs.messages[0]["QueueUrl"].endswith("/commandes")


def test_message_publie_contient_les_donnees_et_le_meme_id(lambda_a, sqs):
    _, corps = appeler(lambda_a, COMMANDE_VALIDE)
    message = json.loads(sqs.messages[0]["MessageBody"])

    assert message["id"] == corps["id"]
    assert message["client"] == "Alice"
    assert message["produit"] == "Clavier"
    assert message["quantite"] == 2
    assert "recu_le" in message


def test_deux_appels_produisent_deux_id_differents(lambda_a, sqs):
    _, premier = appeler(lambda_a, COMMANDE_VALIDE)
    _, second = appeler(lambda_a, COMMANDE_VALIDE)

    assert premier["id"] != second["id"]
    assert len(sqs.messages) == 2


def test_corps_base64_est_decode(lambda_a, sqs):
    code, _ = appeler(lambda_a, COMMANDE_VALIDE, base64_encode=True)
    assert code == 202


@pytest.mark.parametrize("corps", ['{"client": "Alice"', "pas du json", "", "[1, 2]"])
def test_json_invalide_repond_400_sans_publier(lambda_a, sqs, corps):
    code, reponse = appeler(lambda_a, corps)

    assert code == 400
    assert reponse["erreurs"]
    assert sqs.messages == []


@pytest.mark.parametrize("champ", ["client", "produit", "quantite"])
def test_champ_manquant_repond_400(lambda_a, sqs, champ):
    commande = {k: v for k, v in COMMANDE_VALIDE.items() if k != champ}
    code, reponse = appeler(lambda_a, commande)

    assert code == 400
    assert any(champ in erreur for erreur in reponse["erreurs"])
    assert sqs.messages == []


@pytest.mark.parametrize("quantite", [0, -1, 1001, "2", 2.5, True, None])
def test_quantite_invalide_repond_400(lambda_a, sqs, quantite):
    code, _ = appeler(lambda_a, {**COMMANDE_VALIDE, "quantite": quantite})
    assert code == 400
    assert sqs.messages == []


@pytest.mark.parametrize("client", ["", "   ", "x" * 101, 42])
def test_client_invalide_repond_400(lambda_a, sqs, client):
    code, _ = appeler(lambda_a, {**COMMANDE_VALIDE, "client": client})
    assert code == 400


def test_erreur_sqs_repond_500(lambda_a, sqs):
    sqs.erreur = RuntimeError("SQS indisponible")
    code, reponse = appeler(lambda_a, COMMANDE_VALIDE)

    assert code == 500
    assert reponse["erreurs"]