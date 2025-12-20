#!/bin/bash

# Étape 1: Lire la config actuelle et voir tous les paramètres
echo "📖 Configuration actuelle:"
vault read -ns ${VAULT_NAMESPACE} auth/gitlab_cl_oidc/role/images-audit-cl

# Étape 2: Écrire la NOUVELLE config avec TOUS les paramètres
echo ""
echo "✍️  Mise à jour avec nginx ajouté..."
vault write -ns ${VAULT_NAMESPACE} auth/gitlab_cl_oidc/role/images-audit-cl \
  role_type=jwt \
  user_claim=user_email \
  bound_audiences="${VAULT_BOUND_AUDIENCES}" \
  bound_claims='{"project_path":["market-place/ap43590/images/audit","market-place/ap43590/images/nginx"]}' \
  policies=images-audit-cl \
  token_explicit_max_ttl=3600 \
  token_ttl=0s \
  token_max_ttl=0s \
  token_policies=images-audit-cl

# Étape 3: Vérifier le résultat
echo ""
echo "✅ Nouvelle configuration:"
vault read -ns ${VAULT_NAMESPACE} auth/gitlab_cl_oidc/role/images-audit-cl | grep -A2 bound_claims
