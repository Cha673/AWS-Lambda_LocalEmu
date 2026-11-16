# Format des données échangées

Le scénario est l'enregistrement d'une **commande**.

## 1. Requête HTTP (Client → API Gateway → Lambda A)

`POST /commandes`, en-tête `Content-Type: application/json`

```json
{
  "client": "Alice",
  "produit": "Clavier",
  "quantite": 2
}
```

| Champ      | Type   | Règle                                     |
| ---------- | ------ | ----------------------------------------- |
| `client`   | texte  | obligatoire, non vide, 100 caractères max |
| `produit`  | texte  | obligatoire, non vide, 100 caractères max |
| `quantite` | entier | obligatoire, entre 1 et 1000              |

### Réponses de la Lambda A

- **202 Accepted** : la commande est valide et a été déposée dans la file.
  Le traitement n'est **pas encore terminé** à ce moment-là.

  ```json
  {
    "id": "3f2b…",
    "statut": "accepte",
    "message": "Commande reçue, traitement asynchrone en cours"
  }
  ```

- **400 Bad Request** : JSON invalide ou champs incorrects.

  ```json
  { "erreurs": ["quantite doit être un entier entre 1 et 1000"] }
  ```

- **500 Internal Server Error** : le message n'a pas pu être publié dans SQS.

## 2. Message SQS (Lambda A → SQS → Lambda B)

La Lambda A ajoute un identifiant unique et la date de réception :

```json
{
  "id": "3f2b9c1e-8a4d-4f7e-9b2a-1c5d6e7f8a9b",
  "client": "Alice",
  "produit": "Clavier",
  "quantite": 2,
  "recu_le": "2026-11-16T10:15:30.123456+00:00"
}
```

## 3. Item DynamoDB (Lambda B → table `Commandes`)

- Clé de partition : `id` (texte)
- Mode de facturation : à la demande (`PAY_PER_REQUEST`)

```json
{
  "id": "3f2b9c1e-8a4d-4f7e-9b2a-1c5d6e7f8a9b",
  "client": "Alice",
  "produit": "Clavier",
  "quantite": 2,
  "recu_le": "2026-11-16T10:15:30.123456+00:00",
  "traite_le": "2026-11-16T10:15:31.456789+00:00",
  "sqs_message_id": "a1b2c3d4-…"
}
```

L'écart entre `recu_le` et `traite_le` montre que le traitement est asynchrone.

L'`id` étant la clé, la Lambda B n'écrit l'item que s'il n'existe pas déjà.
Un message reçu deux fois ne crée donc pas de doublon : c'est l'**idempotence**.
