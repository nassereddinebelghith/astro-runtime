# Airflow Pre-Login Plugin - Installation Guide

## 📋 Vue d'ensemble

Ce plugin Airflow crée une page de landing custom qui s'affiche avant l'authentification Keycloak.

## 📁 Structure des fichiers

```
plugins/
├── prelogin_plugin.py          # Plugin Flask qui gère les routes
└── templates/
    └── prelogin.html           # Template HTML de la page de landing
```

## 🚀 Installation

### Option 1 : Installation manuelle

1. **Copier les fichiers dans ton environnement Airflow**

```bash
# Sur ton serveur Airflow
cd $AIRFLOW_HOME

# Copier le plugin
cp prelogin_plugin.py plugins/

# Copier le template
mkdir -p plugins/templates
cp templates/prelogin.html plugins/templates/
```

### Option 2 : Avec Astronomer (GitOps)

1. **Ajouter à ton repo Astronomer**

```bash
# Dans ton repo Astronomer
mkdir -p plugins/templates

# Copier les fichiers
cp prelogin_plugin.py plugins/
cp templates/prelogin.html plugins/templates/

# Commit et push
git add plugins/
git commit -m "feat: Add pre-login custom page"
git push origin main
```

2. **Déployer avec Astronomer CLI**

```bash
astro deploy
```

### Option 3 : Avec Kubernetes/Helm

Ajoute dans ton `values.yaml` Helm chart :

```yaml
airflow:
  extraConfigmapMounts:
    - name: prelogin-plugin
      mountPath: /opt/airflow/plugins/prelogin_plugin.py
      subPath: prelogin_plugin.py
      configMap: prelogin-configmap
    - name: prelogin-template
      mountPath: /opt/airflow/plugins/templates/prelogin.html
      subPath: prelogin.html
      configMap: prelogin-configmap
```

Créer le ConfigMap :

```bash
kubectl create configmap prelogin-configmap \
  --from-file=prelogin_plugin.py=plugins/prelogin_plugin.py \
  --from-file=prelogin.html=plugins/templates/prelogin.html \
  -n <ton-namespace>
```

## ⚙️ Configuration

### 1. Vérifier que ton CustomKeycloakAuthManager est configuré

Dans `airflow.cfg` ou via variables d'environnement :

```ini
[core]
auth_manager = path.to.your.CustomKeycloakAuthManager
```

Ou :

```bash
export AIRFLOW__CORE__AUTH_MANAGER=path.to.your.CustomKeycloakAuthManager
```

### 2. Redémarrer Airflow webserver

```bash
# Avec systemd
sudo systemctl restart airflow-webserver

# Avec Astronomer
astro dev restart

# Avec Kubernetes
kubectl rollout restart deployment/airflow-webserver -n <namespace>
```

## ✅ Vérification

### 1. Vérifier que le plugin est chargé

```bash
airflow plugins
```

Tu devrais voir :

```
name        | source
------------+----------------------------------
prelogin    | $PLUGINS_FOLDER/prelogin_plugin.py
```

### 2. Tester l'accès

```bash
# Depuis ton navigateur
https://ton-airflow.com/

# Ou avec curl
curl -L http://ton-airflow.com/
```

Tu devrais être redirigé vers `/welcome` et voir la page custom.

### 3. Tester le bouton de login

1. Accéder à `https://ton-airflow.com/`
2. Cliquer sur "Sign in with Keycloak"
3. Tu devrais être redirigé vers Keycloak pour l'authentification

## 🔧 Troubleshooting

### Le plugin ne se charge pas

```bash
# Vérifier les logs du webserver
kubectl logs -f deployment/airflow-webserver -n <namespace>

# Vérifier les permissions des fichiers
ls -la $AIRFLOW_HOME/plugins/
ls -la $AIRFLOW_HOME/plugins/templates/
```

### La page ne s'affiche pas

```bash
# Vérifier que le template est accessible
cat $AIRFLOW_HOME/plugins/templates/prelogin.html

# Tester manuellement la route
curl http://localhost:8080/welcome
```

### Le bouton ne redirige pas vers Keycloak

```python
# Tester la route /login manuellement
from airflow.www.app import create_app
app = create_app()
with app.test_client() as client:
    response = client.get('/login', follow_redirects=False)
    print(f"Status: {response.status_code}")
    print(f"Location: {response.headers.get('Location')}")
```

## 🎨 Personnalisation

### Modifier le design de la page

Édite `plugins/templates/prelogin.html` et modifie :

- Les couleurs dans la section `<style>`
- Le titre et sous-titre
- Le logo emoji (🚀)
- Les features listées

### Modifier le comportement du plugin

Édite `plugins/prelogin_plugin.py` :

- Route `/welcome` : modifier la logique d'affichage
- Route `/start-auth` : modifier la redirection
- Middleware `redirect_root_to_welcome()` : changer les conditions de redirection

## 📊 Flow utilisateur complet

```
1. User accède à https://mon-airflow.com/
                    ↓
2. Middleware détecte "/" + non authentifié
                    ↓
3. Redirect automatique vers /welcome
                    ↓
4. Affichage de prelogin.html
                    ↓
5. User clique sur "Sign in with Keycloak"
                    ↓
6. Redirect vers /start-auth
                    ↓
7. Redirect vers /login
                    ↓
8. CustomKeycloakAuthManager prend le relais
                    ↓
9. Redirect vers Keycloak OIDC
                    ↓
10. User s'authentifie sur Keycloak
                    ↓
11. Callback vers Airflow /oauth-authorized
                    ↓
12. User authentifié → Redirect vers /home
                    ↓
13. ✅ User accède à Airflow avec ses workflows
```

## 📝 Notes importantes

- Le plugin utilise Flask Blueprint pour s'intégrer proprement dans Airflow
- La redirection "/" → "/welcome" se fait uniquement pour les utilisateurs non authentifiés
- Les routes statiques (`/static`) et API (`/api`) ne sont pas affectées
- Le template est responsive et fonctionne sur mobile
- Compatible avec Airflow 3.x et le nouveau système Auth Manager

## 🆘 Support

Si tu rencontres des problèmes :

1. Vérifie les logs Airflow : `airflow-webserver.log`
2. Teste chaque route individuellement : `/welcome`, `/start-auth`, `/login`
3. Vérifie que ton CustomKeycloakAuthManager fonctionne sans le plugin

## 📄 Licence

Ce plugin est fourni tel quel pour une utilisation interne.
