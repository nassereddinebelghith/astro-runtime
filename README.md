# Configuration Nginx Prelogin pour Airflow 3.1.4 + Keycloak

## Vue d’ensemble

Cette configuration permet d’afficher une page d’accueil personnalisée **avant** l’authentification Keycloak, au lieu du 401 par défaut.

## Architecture

```
User → Ingress → nginx-prelogin → Airflow
                      ↓
                (intercepte 401)
                      ↓
                Landing Page
```

### Comment ça marche

1. **User accède à `/`** → nginx proxy vers Airflow
1. **Airflow retourne 401** (pas authentifié)
1. **nginx intercepte le 401** → sert la landing page
1. **User clique “Se connecter”** → `/login` → Auth Keycloak normale
1. **Après auth** → User arrive sur Airflow avec les bons droits

## Fichiers

- `all-in-one-nginx-prelogin.yaml` - **Fichier complet** (ConfigMap + Deployment + Service)
- `nginx-prelogin-configmap.yaml` - ConfigMap seul
- `nginx-prelogin-deployment.yaml` - Deployment + Service
- `web-ingress.yaml` - Ingress mis à jour

## Déploiement

### Option 1: Déploiement rapide (recommandé)

```bash
# Déploie tout en une commande
kubectl apply -f all-in-one-nginx-prelogin.yaml

# Vérifie que les pods démarrent
kubectl get pods -n airflow -l app=nginx-prelogin

# Vérifie le service
kubectl get svc -n airflow nginx-prelogin
```

### Option 2: Déploiement par étapes

```bash
# 1. ConfigMap
kubectl apply -f nginx-prelogin-configmap.yaml

# 2. Deployment et Service
kubectl apply -f nginx-prelogin-deployment.yaml

# 3. Vérifie le déploiement
kubectl get all -n airflow -l app=nginx-prelogin
```

### Mise à jour de l’Ingress

**IMPORTANT**: Remplace ton fichier `web/web-ingress.yaml` existant par le nouveau.

Si tu utilises Helm:

```bash
# Copie le nouveau web-ingress.yaml dans ton chart
cp web-ingress.yaml /path/to/your/helm/chart/templates/

# Upgrade Helm
helm upgrade airflow ./your-chart -n airflow
```

## Configuration requise

### ⚠️ IMPORTANT: Nom du service Airflow

Dans `nginx.conf`, tu dois remplacer le nom du service Airflow par le vrai nom:

```nginx
upstream airflow {
    # Remplace par le vrai nom de ton service
    server astronomer-webserver:8080;
}
```

Pour trouver le bon nom:

```bash
kubectl get svc -n airflow | grep webserver
```

Exemples possibles:

- `astronomer-webserver.airflow.svc.cluster.local:8080`
- `airflow-webserver:8080`
- `mon-airflow-webserver:8080`

## Vérification

### 1. Vérifie que nginx est UP

```bash
kubectl get pods -n airflow -l app=nginx-prelogin
# Devrait montrer 2 pods en Running
```

### 2. Vérifie les logs nginx

```bash
kubectl logs -n airflow -l app=nginx-prelogin -f
```

### 3. Test depuis un pod

```bash
kubectl run -it --rm debug --image=curlimages/curl --restart=Never -n airflow -- sh

# Dans le pod:
curl http://nginx-prelogin
# Devrait retourner le HTML de la landing page

curl -I http://nginx-prelogin/login
# Devrait retourner 302 (redirection Keycloak)
```

### 4. Test depuis le navigateur

1. Va sur ton URL Airflow: `https://airflow.monentreprise.com/`
1. Tu devrais voir la landing page 🚀
1. Clique “Se connecter via SSO”
1. Authentification Keycloak (Touch ID / code PIN)
1. Redirection vers Airflow

## Troubleshooting

### Bad Gateway 502

**Cause**: Le nom du service Airflow est incorrect dans nginx.conf

**Solution**:

```bash
# 1. Trouve le bon nom
kubectl get svc -n airflow | grep webserver

# 2. Édite le ConfigMap
kubectl edit configmap nginx-prelogin-config -n airflow

# 3. Modifie la ligne "server astronomer-webserver:8080;"
#    avec le bon nom de service

# 4. Redémarre nginx
kubectl rollout restart deployment/nginx-prelogin -n airflow
```

### La landing page ne s’affiche pas

**Cause**: Le 401 n’est pas intercepté

**Vérification**:

```bash
# Vérifie la config nginx
kubectl exec -n airflow deployment/nginx-prelogin -- cat /etc/nginx/nginx.conf | grep "proxy_intercept_errors"
# Doit retourner: proxy_intercept_errors on;
```

### Boucle de redirection infinie

**Cause**: L’Ingress pointe toujours vers le service Airflow au lieu de nginx-prelogin

**Solution**:

```bash
# Vérifie l'Ingress
kubectl get ingress -n airflow -o yaml | grep "name:"
# Doit montrer: name: nginx-prelogin
```

## Personnalisation

### Modifier la landing page

1. Édite le ConfigMap:

```bash
kubectl edit configmap nginx-prelogin-config -n airflow
```

1. Modifie la section `index.html`
1. Redémarre nginx:

```bash
kubectl rollout restart deployment/nginx-prelogin -n airflow
```

### Changer les couleurs

Dans `index.html`, modifie les gradients:

```css
background: linear-gradient(135deg, #017CEE 0%, #764ba2 100%);
```

### Ajouter un logo

Remplace l’emoji 🚀 par une image:

```html
<img src="/static/logo.png" alt="Logo" style="width: 120px;" />
```

## Support

Si tu as des problèmes:

1. Vérifie les logs: `kubectl logs -n airflow -l app=nginx-prelogin`
1. Vérifie le service Airflow: `kubectl get svc -n airflow`
1. Test le proxy nginx: `kubectl exec -n airflow deployment/nginx-prelogin -- curl -I http://astronomer-webserver:8080`

## Architecture détaillée

```
┌─────────────────────────────────────────────────────┐
│                    Ingress                          │
│            (https://airflow.domain.com)             │
└──────────────────────┬──────────────────────────────┘
                       │
                       │ path: /
                       ▼
┌─────────────────────────────────────────────────────┐
│              Service: nginx-prelogin                │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│           Deployment: nginx-prelogin                │
│                                                     │
│  ┌───────────────────────────────────────────┐    │
│  │  nginx.conf:                              │    │
│  │  - error_page 401 = @landing_page         │    │
│  │  - location / → proxy_pass airflow        │    │
│  │  - proxy_intercept_errors on              │    │
│  └───────────────────────────────────────────┘    │
└──────────────┬────────────────────┬─────────────────┘
               │                    │
        User demande /       Airflow répond 401
               │                    │
               ▼                    ▼
     ┌──────────────────┐   ┌─────────────────┐
     │ Service: Airflow │   │  Landing Page   │
     │   (webserver)    │   │   (index.html)  │
     └──────────────────┘   └─────────────────┘
```

## Notes importantes

- **Namespace**: Tous les fichiers utilisent le namespace `airflow`. Modifie si nécessaire.
- **Replicas**: 2 réplicas nginx pour la haute disponibilité
- **Resources**: Limites CPU/Memory configurées pour un usage léger
- **Health checks**: Liveness et Readiness probes configurés

## Prochaines étapes

1. ✅ Déployer nginx-prelogin
1. ✅ Mettre à jour l’Ingress
1. ⚠️ **Ajuster le nom du service Airflow dans nginx.conf**
1. ✅ Tester l’accès
1. 🎨 Personnaliser la landing page selon tes besoins
