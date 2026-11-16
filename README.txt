# TP 1 bis – Architecture serverless AWS 100 % locale (LocalEmu)

Variante AWS du TP Azure Functions : une architecture événementielle
**HTTP → Lambda → SQS → Lambda → DynamoDB**, exécutée entièrement sur le poste
avec **LocalEmu**, sans compte AWS, sans carte bancaire et sans aucun déploiement distant.

Scénario métier volontairement simple : l'enregistrement d'une **commande**
(client, produit, quantité).

---

## 1. Architecture

```
Client HTTP (curl)
     │  POST /commandes  {"client": "...", "produit": "...", "quantite": 2}
     ▼
API Gateway (local)            ← expose la route HTTP, intégration AWS_PROXY
     │
     ▼
Lambda A  lambda-a-http        ← valide, publie dans SQS, répond 202 / 400 / 500
     │  sqs:SendMessage
     ▼
SQS  commandes                 ← transport asynchrone, découplage
     │  event source mapping (déclenchement automatique)
     │  après 3 échecs ──► SQS commandes-dlq (file d'erreurs)
     ▼
Lambda B  lambda-b-sqs         ← lit le lot de messages, écrit sans doublon
     │  dynamodb:PutItem (conditionnel)
     ▼
DynamoDB  Commandes            ← persistance (clé : id)
```

| Composant | Rôle |
|---|---|
| **API Gateway** | Expose `POST /commandes` en local et transmet la requête complète à la Lambda A (intégration `AWS_PROXY`). |
| **Lambda A** (`src/lambda_a_http`) | Fonction sans état : lit et valide le JSON, génère un `id` unique, publie le message dans SQS et répond **202 Accepted**, ou **400** si les données sont invalides. Elle n'écrit pas dans DynamoDB et n'appelle pas la Lambda B. |
| **SQS** `commandes` | Découple les deux fonctions et conserve les messages tant qu'ils ne sont pas traités. Après 3 échecs, un message part dans `commandes-dlq`. |
| **Lambda B** (`src/lambda_b_sqs`) | Déclenchée **automatiquement** par SQS via un *event source mapping*. Traite les messages par lots, écrit dans DynamoDB de façon idempotente et signale les messages en échec (`batchItemFailures`). |
| **DynamoDB** `Commandes` | Stocke les commandes traitées. `recu_le` (Lambda A) et `traite_le` (Lambda B) montrent le décalage asynchrone. |

Le format exact des données est décrit dans [`docs/format-donnees.md`](docs/format-donnees.md).

---

## 2. Organisation du dépôt

```
.
├── README.md
├── requirements-dev.txt        # outils de test (pytest, boto3)
├── docs/
│   ├── format-donnees.md       # format HTTP → SQS → DynamoDB
│   └── preuves/                # preuves d'exécution (sorties, captures)
├── infra/
│   ├── trust-policy.json       # qui peut utiliser le rôle (service Lambda)
│   └── lambda-policy.json      # droits minimaux des Lambda
├── scripts/
│   ├── config.sh               # noms des ressources + vérification de la cible locale
│   └── deploy.sh               # création / mise à jour de toutes les ressources
├── src/
│   ├── lambda_a_http/handler.py
│   └── lambda_b_sqs/handler.py
└── tests/                      # tests unitaires (sans émulateur)
```

---

## 3. Prérequis

| Outil | Version | Remarque |
|---|---|---|
| Python | 3.10 ou plus | pour LocalEmu, `awsemu` et les tests |
| Docker | Docker Desktop (Windows/macOS) ou Docker Engine (Linux) | exécute LocalEmu et les conteneurs des Lambda |
| Git | — | |
| curl | — | pour appeler l'API |

**Sous Windows :** tout se fait dans **WSL2 (Ubuntu)**, avec Docker Desktop configuré
pour utiliser le moteur WSL2 et l'intégration Ubuntu activée
(*Settings → Resources → WSL Integration*). Le projet doit être placé dans le
système de fichiers Linux (`~/…`), et non dans `/mnt/c/…`.

Aucun compte AWS ni aucun compte LocalEmu n'est nécessaire.

---

## 4. Lancement local (procédure pour un tiers)

### 4.1 Installation (une seule fois)

```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip git curl

python3 -m venv ~/localemu-env
source ~/localemu-env/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install "localemu[runtime]"
python3 -m pip install -r requirements-dev.txt

# Réseau Docker partagé par LocalEmu et les conteneurs Lambda
docker network create localemu-net

# Image du runtime Lambda, téléchargée à l'avance
docker pull public.ecr.aws/lambda/python:3.12
```

### 4.2 Démarrer LocalEmu (terminal 1, à laisser ouvert)

```bash
docker run --rm -it \
  --name localemu \
  --network localemu-net \
  -p 4566:4566 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e LAMBDA_DOCKER_NETWORK=localemu-net \
  -e LAMBDA_RUNTIME_ENVIRONMENT_TIMEOUT=120 \
  localemu/localemu
```

> Pourquoi pas `localemu start` ? Voir la section 7 (problèmes rencontrés).
> Sur une machine Linux native, `localemu start` peut suffire.

Pour l'arrêter : `docker stop localemu`.

### 4.3 Créer les ressources (terminal 2)

```bash
source ~/localemu-env/bin/activate
cd TP1_bis_AWS
./scripts/deploy.sh
```

Le script :

1. vérifie que la cible est bien l'émulateur (compte `000000000000`), sinon il s'arrête ;
2. crée les files SQS `commandes` et `commandes-dlq` ;
3. crée la table DynamoDB `Commandes` ;
4. crée le rôle IAM des Lambda ;
5. empaquette et déploie `lambda-a-http` et `lambda-b-sqs` ;
6. relie SQS à la Lambda B (*event source mapping*) ;
7. crée l'API REST et la route `POST /commandes` ;
8. écrit l'URL de l'API dans `.env.local` (fichier non versionné).

Il est **idempotent** : on peut le relancer sans risque. LocalEmu ne conservant
rien à l'arrêt, **il faut le relancer après chaque redémarrage de l'émulateur**.

---

## 5. Tester le parcours HTTP → SQS → DynamoDB

### 5.1 Tests unitaires (sans émulateur)

```bash
python3 -m pytest -v
```

Ils vérifient la validation, les codes HTTP, l'idempotence de la Lambda B et la
gestion des échecs partiels, avec SQS et DynamoDB simulés.

### 5.2 Appel valide

```bash
source .env.local
curl -i -X POST "$API_URL" \
  -H "Content-Type: application/json" \
  -d '{"client": "Alice", "produit": "Clavier", "quantite": 2}'
```

Réponse attendue : `HTTP 202` avec un `id`. Quelques secondes plus tard :

```bash
awsemu dynamodb get-item --table-name Commandes \
  --key '{"id": {"S": "ID_RENVOYÉ_PAR_LA_RÉPONSE"}}'
```

### 5.3 Appel invalide

```bash
curl -i -X POST "$API_URL" \
  -H "Content-Type: application/json" \
  -d '{"client": "Bob", "produit": "Stylo", "quantite": -3}'
```

Réponse attendue : `HTTP 400` avec la liste des erreurs. Rien n'est envoyé dans SQS.

### 5.4 Consulter l'état

```bash
awsemu dynamodb scan --table-name Commandes
awsemu sqs get-queue-attributes --queue-url "$QUEUE_URL" --attribute-names All
awsemu logs filter-log-events --log-group-name /aws/lambda/lambda-a-http --query 'events[].message' --output text
awsemu logs filter-log-events --log-group-name /aws/lambda/lambda-b-sqs  --query 'events[].message' --output text
```

Tableau de bord de l'émulateur : http://localhost:4566/_localemu/dashboard

Toutes les preuves d'exécution sont rassemblées dans
[`docs/preuves/PREUVES.md`](docs/preuves/PREUVES.md).

---

## 6. Comment prouver que l'on cible l'émulateur et non AWS ?

- Toutes les commandes passent par **`awsemu`**, qui cible `http://localhost:4566`.
  Dans les scripts, elles passent par une seule fonction, `aws_local`.
- `awsemu sts get-caller-identity` renvoie le **compte simulé `000000000000`** ;
  `deploy.sh` refuse de continuer si ce n'est pas le cas.
- Les réponses HTTP de l'API contiennent l'en-tête **`x-localemu: true`**.
- Aucun identifiant AWS réel n'existe sur le poste ni dans le dépôt.
- Les Lambda s'exécutent dans des conteneurs Docker visibles avec `docker ps`.

---

## 7. Problèmes rencontrés et solutions

| Problème | Cause | Solution |
|---|---|---|
| `ensurepip is not available` à la création du venv | Paquet `python3-venv` absent d'Ubuntu | `sudo apt install python3.12-venv` |
| `Permission denied` lors de la création du dossier | Commande lancée dans `/home` au lieu de `/home/<utilisateur>` | `cd ~` avant de créer le projet |
| `fixture 'lambda_a' not found` | `tests/conftest.py` présent mais vide (non enregistré) | Contenu recollé et enregistré |
| `Unknown options: --cli-binary-format` | `awsemu` s'appuie sur l'AWS CLI **v1**, qui n'a pas cette option | Option retirée (inutile en v1) |
| Appel HTTP bloqué, puis **502 Bad Gateway** | Avec `localemu start` dans WSL2, les conteneurs Lambda lancés par Docker Desktop ne parvenaient pas à joindre l'émulateur : *« timed out during startup »* | LocalEmu lancé **dans Docker**, sur un réseau dédié (`localemu-net`) partagé avec les Lambda, avec un délai de démarrage porté à 120 s |
| `Missing Authentication Token` dans le navigateur | Le navigateur envoie un **GET** alors que seule la route **POST** existe | Tester avec `curl -X POST` |

---

## 8. Limites identifiées

- **Pas de persistance** : toutes les ressources disparaissent à l'arrêt de LocalEmu,
  d'où le script `deploy.sh`.
- **L'URL de l'API change à chaque déploiement** (nouvel identifiant d'API) ;
  elle est relue depuis `.env.local`.
- **IAM non appliqué** par LocalEmu par défaut : les politiques sont définies mais pas
  contrôlées. De plus, un **seul rôle** est partagé par les deux Lambda. En production,
  chaque fonction aurait son propre rôle au moindre privilège.
- **API sans authentification** ni limitation de débit.
- **Fidélité de l'émulateur** : le comportement local (délais, réseau, URL d'API)
  peut différer d'AWS.
- La Lambda B contrôle seulement la présence des champs. La validation complète est faite
  par la Lambda A, ce qui suppose que seule la Lambda A écrit dans la file.
- Aucune route ne permet de consulter le statut d'une commande : il faut interroger
  DynamoDB directement.

---

## 9. Questions d'analyse

### Pourquoi ne pas écrire directement dans DynamoDB depuis la Lambda HTTP ?

- **Temps de réponse** : le client n'attend pas l'écriture en base ni un éventuel
  traitement plus long ; la Lambda A répond dès que le message est en file.
- **Résilience** : si DynamoDB est lent ou indisponible, les requêtes ne sont pas perdues.
  Elles restent dans SQS et sont retraitées plus tard, au lieu de renvoyer des erreurs 500.
- **Responsabilité unique** : la Lambda A reçoit, la Lambda B traite. On peut faire
  évoluer, redéployer ou faire monter en charge l'une sans toucher à l'autre.
- **C'est une contrainte de l'architecture demandée** : la communication doit être
  asynchrone.

### Quel est l'intérêt de SQS entre les deux fonctions ?

- **Découplage** : les deux fonctions ne se connaissent pas ; elles partagent
  seulement le format du message.
- **Tampon** : en cas de pic, les messages s'accumulent dans la file et la Lambda B
  les consomme à son rythme, par lots.
- **Fiabilité** : un message n'est supprimé qu'une fois traité avec succès.
  En cas d'échec, il est retenté, puis isolé dans la **DLQ** après 3 tentatives.
- **Déclenchement automatique** : l'*event source mapping* interroge la file et
  invoque la Lambda B. Personne ne l'appelle à la main.

### Quels problèmes peuvent survenir si un message est traité plusieurs fois ?

SQS (file standard) garantit une livraison **au moins une fois**. Un même message peut
donc être reçu deux fois : délai de visibilité dépassé, Lambda arrêtée après l'écriture
mais avant l'acquittement, lot partiellement en échec…

- **Doublons** : deux commandes enregistrées au lieu d'une, si l'identifiant était
  généré au moment du traitement.
- **Écrasement** : un second traitement peut écraser des données plus récentes.
- **Effets de bord répétés** : double facturation, double envoi d'e-mail, stock
  décrémenté deux fois…

**Solution retenue : l'idempotence.** L'`id` est généré une seule fois par la Lambda A
et voyage dans le message. La Lambda B écrit avec
`ConditionExpression="attribute_not_exists(id)"` : un second traitement est refusé par
DynamoDB, journalisé (`doublon_ignore`) et considéré comme un succès, donc le message
n'est pas retenté. Une file **FIFO** avec déduplication serait une alternative.

### Quelles différences avec Azure Functions, Azure Queue Storage et Azure Table Storage ?

| | Azure | AWS |
|---|---|---|
| **Fonctions** | Azure Functions, organisées dans une *Function App* | AWS Lambda : chaque fonction est une ressource indépendante |
| **Exposition HTTP** | Déclencheur HTTP **intégré** à la fonction | Service séparé : **API Gateway**, à configurer (route, intégration, permission) |
| **Liaison avec la file** | **Triggers et bindings déclaratifs** (`function.json` ou décorateurs) : la sortie vers la file ou la table se déclare sans code SDK | **Event source mapping** pour le déclenchement, et appels explicites via le **SDK** (boto3) pour écrire |
| **File de messages** | Azure Queue Storage, intégrée à un compte de stockage, fonctionnalités simples | Amazon SQS, service dédié : files standard ou FIFO, délai de visibilité, *long polling* |
| **Messages en échec** | File `<nom>-poison` créée **automatiquement** après 5 tentatives | **DLQ** à créer et à relier **explicitement** (`RedrivePolicy`) |
| **Échecs partiels** | Chaque message déclenche sa propre exécution | Traitement **par lots**, avec `batchItemFailures` pour ne rejouer que les messages en échec |
| **Base NoSQL** | Azure Table Storage : clé imposée **PartitionKey + RowKey**, fonctionnalités limitées | DynamoDB : clé de partition (et de tri optionnelle), **écritures conditionnelles**, index secondaires, streams |
| **Émulation locale** | Azurite (stockage) + Azure Functions Core Tools | LocalEmu (tous les services) + Docker pour exécuter les Lambda |
| **Droits** | Chaînes de connexion / identités managées | Rôles **IAM** attachés à chaque Lambda |

En résumé, les concepts sont équivalents (fonction, file, table, déclencheur).
Azure privilégie la **configuration déclarative** et des valeurs par défaut, tandis
qu'AWS demande de **relier explicitement** chaque service, mais offre un contrôle plus fin.

