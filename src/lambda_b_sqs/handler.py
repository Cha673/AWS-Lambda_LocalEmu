"""Lambda B : déclenchée par SQS, enregistre les commandes dans DynamoDB.

Fonction indépendante de la Lambda A : elle ne connaît que le format
du message SQS, pas le code qui l'a produit.
"""

import json
import logging
import os
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

CHAMPS_OBLIGATOIRES = ("id", "client", "produit", "quantite", "recu_le")

_table = None  # ressource créée au premier appel puis réutilisée


def _endpoint_url():
    """Adresse de l'émulateur vue depuis le conteneur Lambda (None sur un vrai AWS)."""
    for variable in ("AWS_ENDPOINT_URL_DYNAMODB", "AWS_ENDPOINT_URL"):
        if os.environ.get(variable):
            return os.environ[variable]
    hote = os.environ.get("LOCALEMU_HOSTNAME") or os.environ.get("LOCALSTACK_HOSTNAME")
    if hote:
        return f"http://{hote}:{os.environ.get('EDGE_PORT', '4566')}"
    return None


def _table_dynamodb():
    global _table
    if _table is None:
        dynamodb = boto3.resource("dynamodb", endpoint_url=_endpoint_url())
        _table = dynamodb.Table(os.environ["TABLE_NAME"])
    return _table


def _log(niveau, evenement, **details):
    logger.log(niveau, json.dumps({"evenement": evenement, **details}, ensure_ascii=False))


def lire_message(record):
    """Transforme un record SQS en commande. Lève ValueError si le contenu est invalide."""
    try:
        commande = json.loads(record["body"])
    except (KeyError, ValueError) as exc:
        raise ValueError("corps du message absent ou non JSON") from exc

    if not isinstance(commande, dict):
        raise ValueError("le message doit être un objet JSON")
    manquants = [champ for champ in CHAMPS_OBLIGATOIRES if champ not in commande]
    if manquants:
        raise ValueError(f"champs manquants : {', '.join(manquants)}")
    return commande


def enregistrer(commande, record):
    """Écrit la commande dans DynamoDB. Retourne False si elle existait déjà."""
    item = {
        "id": commande["id"],
        "client": commande["client"],
        "produit": commande["produit"],
        "quantite": commande["quantite"],
        "recu_le": commande["recu_le"],
        "traite_le": datetime.now(timezone.utc).isoformat(),
        "sqs_message_id": record.get("messageId"),
    }
    try:
        # N'écrit que si aucun item ne porte déjà cet id : idempotence
        _table_dynamodb().put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(id)",
        )
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def handler(event, context):
    echecs = []
    records = event.get("Records", [])
    _log(logging.INFO, "lot_recu", nombre_messages=len(records))

    for record in records:
        message_id = record.get("messageId")
        nb_receptions = record.get("attributes", {}).get("ApproximateReceiveCount")
        try:
            commande = lire_message(record)
            if enregistrer(commande, record):
                _log(logging.INFO, "commande_enregistree",
                     id=commande["id"], sqs_message_id=message_id, receptions=nb_receptions)
            else:
                _log(logging.WARNING, "doublon_ignore",
                     id=commande["id"], sqs_message_id=message_id, receptions=nb_receptions)
        except ValueError as exc:
            _log(logging.ERROR, "message_invalide",
                 sqs_message_id=message_id, erreur=str(exc), receptions=nb_receptions)
            echecs.append({"itemIdentifier": message_id})
        except Exception:
            logger.exception("Erreur inattendue sur le message %s", message_id)
            echecs.append({"itemIdentifier": message_id})

    if echecs:
        _log(logging.WARNING, "lot_partiellement_en_echec", echecs=len(echecs))

    # Seuls les messages listés ici retournent dans la file pour être retentés
    return {"batchItemFailures": echecs}