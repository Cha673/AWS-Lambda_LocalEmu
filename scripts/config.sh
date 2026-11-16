#!/usr/bin/env bash
# Configuration commune à tous les scripts.
# Ce fichier n'est pas exécuté directement : les autres scripts le chargent avec "source".

# --- Cible : l'émulateur local uniquement ---
ENDPOINT="http://localhost:4566"
COMPTE_LOCAL="000000000000"          # compte simulé de LocalEmu
export AWS_DEFAULT_REGION="us-east-1"
export AWS_PAGER=""                  # évite que les résultats s'ouvrent dans "less"

# --- Noms des ressources ---
QUEUE_NAME="commandes"
DLQ_NAME="commandes-dlq"
TABLE_NAME="Commandes"
ROLE_NAME="lambda-commandes-role"
LAMBDA_A="lambda-a-http"
LAMBDA_B="lambda-b-sqs"
RUNTIME="python3.12"
API_NAME="api-commandes"
STAGE="dev"

# --- Fichiers locaux (ignorés par git) ---
BUILD_DIR="build"
ENV_FILE=".env.local"

# Toutes les commandes AWS passent par awsemu, qui cible LocalEmu.
aws_local() {
    awsemu "$@"
}

# Refuse de continuer si on ne parle pas à l'émulateur local.
verifier_emulateur() {
    if ! command -v awsemu >/dev/null 2>&1; then
        echo "ERREUR : commande awsemu introuvable. As-tu activé le venv ?"
        echo "         source ~/localemu-env/bin/activate"
        exit 1
    fi

    local compte
    if ! compte=$(aws_local sts get-caller-identity --query Account --output text 2>/dev/null); then
        echo "ERREUR : LocalEmu ne répond pas. Lance 'localemu start' dans un autre terminal."
        exit 1
    fi

    if [[ "$compte" != "$COMPTE_LOCAL" ]]; then
        echo "ERREUR : compte '$compte' détecté au lieu de $COMPTE_LOCAL."
        echo "         Ce n'est pas l'émulateur local : arrêt par sécurité."
        exit 1
    fi

    echo "✔ Cible vérifiée : LocalEmu sur $ENDPOINT (compte simulé $compte, région $AWS_DEFAULT_REGION)"
}