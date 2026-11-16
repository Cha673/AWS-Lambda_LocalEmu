"""Lambda A : reçoit une commande via API Gateway et la publie dans SQS.

Fonction sans état (stateless) : elle ne garde rien entre deux appels,
n'écrit pas dans DynamoDB et n'appelle pas la Lambda B.
"""

import base64
import json
import logging
import os
import uuid
from datetime import datetime, timezone

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

LONGUEUR_MAX = 100
QUANTITE_MIN = 1
QUANTITE_MAX = 1000

_sqs = None  # client créé au premier appel puis réutilisé


def _endpoint_url():
    """Retourne l'adresse de l'émulateur vue depuis le conteneur Lambda.

    LocalEmu injecte cette information dans les variables d'environnement
    de la Lambda. Sur un vrai AWS, aucune n'existe et boto3 utilise
    l'endpoint par défaut (None).
    """
    for variable in ("AWS_ENDPOINT_URL_SQS", "AWS_ENDPOINT_URL"):
        if os.environ.get(variable):
            return os.environ[variable]
    hote = os.environ.get("LOCALEMU_HOSTNAME") or os.environ.get("LOCALSTACK_HOSTNAME")
    if hote:
        return f"http://{hote}:{os.environ.get('EDGE_PORT', '4566')}"
    return None


def _client_sqs():
    global _sqs
    if _sqs is None:
        _sqs = boto3.client("sqs", endpoint_url=_endpoint_url())
    return _sqs


def _log(niveau, evenement, **details):
    """Écrit un log au format JSON, plus facile à filtrer."""
    logger.log(niveau, json.dumps({"evenement": evenement, **details}, ensure_ascii=False))


def _reponse(code, corps):
    return {
        "statusCode": code,
        "headers": {"Content-Type": "application/json; charset=utf-8"},
        "body": json.dumps(corps, ensure_ascii=False),
    }


def valider(donnees):
    """Retourne la liste des erreurs (liste vide si les données sont valides)."""
    if not isinstance(donnees, dict):
        return ["le corps doit être un objet JSON"]

    erreurs = []
    for champ in ("client", "produit"):
        valeur = donnees.get(champ)
        if not isinstance(valeur, str) or not valeur.strip():
            erreurs.append(f"{champ} est obligatoire et doit être un texte non vide")
        elif len(valeur) > LONGUEUR_MAX:
            erreurs.append(f"{champ} ne doit pas dépasser {LONGUEUR_MAX} caractères")

    quantite = donnees.get("quantite")
    # bool est un sous-type de int en Python : on l'exclut explicitement
    if (
        not isinstance(quantite, int)
        or isinstance(quantite, bool)
        or not QUANTITE_MIN <= quantite <= QUANTITE_MAX
    ):
        erreurs.append(f"quantite doit être un entier entre {QUANTITE_MIN} et {QUANTITE_MAX}")

    return erreurs


def handler(event, context):
    id_requete = getattr(context, "aws_request_id", None)

    # 1. Lire le corps de la requête
    corps = event.get("body") or ""
    try:
        if event.get("isBase64Encoded"):
            corps = base64.b64decode(corps).decode("utf-8")
        donnees = json.loads(corps)
    except ValueError:
        _log(logging.WARNING, "json_invalide", requete=id_requete)
        return _reponse(400, {"erreurs": ["le corps de la requête n'est pas un JSON valide"]})

    # 2. Valider les données
    erreurs = valider(donnees)
    if erreurs:
        _log(logging.WARNING, "donnees_invalides", requete=id_requete, erreurs=erreurs)
        return _reponse(400, {"erreurs": erreurs})

    # 3. Construire le message et le publier dans SQS
    message = {
        "id": str(uuid.uuid4()),
        "client": donnees["client"].strip(),
        "produit": donnees["produit"].strip(),
        "quantite": donnees["quantite"],
        "recu_le": datetime.now(timezone.utc).isoformat(),
    }
    try:
        resultat = _client_sqs().send_message(
            QueueUrl=os.environ["QUEUE_URL"],
            MessageBody=json.dumps(message, ensure_ascii=False),
        )
    except Exception:
        logger.exception("Échec de publication dans SQS (requête %s)", id_requete)
        return _reponse(500, {"erreurs": ["la commande n'a pas pu être enregistrée, réessayez"]})

    _log(
        logging.INFO,
        "message_publie",
        requete=id_requete,
        id=message["id"],
        sqs_message_id=resultat.get("MessageId"),
    )

    # 4. Répondre 202 : reçu, mais le traitement n'est pas encore fait
    return _reponse(202, {
        "id": message["id"],
        "statut": "accepte",
        "message": "Commande reçue, traitement asynchrone en cours",
    })