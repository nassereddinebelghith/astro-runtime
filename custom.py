# ============================================================================
# FICHIER 1: plugins/welcome_page_plugin.py
# Page d'accueil publique avec redirection vers /login pour authentification
# ============================================================================

import os
from airflow.plugins_manager import AirflowPlugin
from flask import Blueprint, render_template_string, redirect

# Blueprint pour la page welcome (publique)
welcome_bp = Blueprint(
    "welcome_page",
    __name__,
    template_folder='templates',
    static_folder='static'
)

@welcome_bp.route('/welcome')
def welcome():
    """Page d'accueil publique avant authentification"""
    version = os.environ.get('VERSION', 'Unknown')
    
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Bienvenue sur Airflow</title>
        <meta charset="UTF-8">
        <style>
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }
            
            body { 
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 20px;
            }
            
            .container {
                background: white;
                padding: 60px 40px;
                border-radius: 16px;
                box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                max-width: 500px;
                width: 100%;
                text-align: center;
                animation: fadeIn 0.6s ease-out;
            }
            
            @keyframes fadeIn {
                from {
                    opacity: 0;
                    transform: translateY(-20px);
                }
                to {
                    opacity: 1;
                    transform: translateY(0);
                }
            }
            
            .logo {
                font-size: 80px;
                margin-bottom: 20px;
                animation: float 3s ease-in-out infinite;
            }
            
            @keyframes float {
                0%, 100% { transform: translateY(0px); }
                50% { transform: translateY(-10px); }
            }
            
            h1 {
                color: #2d3748;
                font-size: 32px;
                margin-bottom: 16px;
                font-weight: 600;
            }
            
            .subtitle {
                color: #718096;
                font-size: 16px;
                margin-bottom: 40px;
                line-height: 1.6;
            }
            
            button {
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                border: none;
                padding: 16px 48px;
                font-size: 18px;
                font-weight: 600;
                border-radius: 8px;
                cursor: pointer;
                transition: all 0.3s ease;
                box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4);
            }
            
            button:hover { 
                transform: translateY(-2px);
                box-shadow: 0 6px 20px rgba(102, 126, 234, 0.6);
            }
            
            button:active {
                transform: translateY(0);
            }
            
            .version {
                margin-top: 40px;
                padding-top: 30px;
                border-top: 1px solid #e2e8f0;
                color: #4a5568;
                font-size: 15px;
                font-weight: 500;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="logo">ð</div>
            <h1>Sign In</h1>
            <p class="subtitle">
                Bienvenue sur la plateforme de donnees Airflow.<br>
                Cliquez ci-dessous pour vous authentifier.
            </p>
            <button onclick="window.location.href='/login'">
                Se connecter
            </button>
            <div class="version">
                DataHUB OaaS v{{ version }}
            </div>
        </div>
    </body>
    </html>
    """
    return render_template_string(html, version=version)


class WelcomePagePlugin(AirflowPlugin):
    name = "welcome_page"
    flask_blueprints = [welcome_bp]
    
    @staticmethod
    def on_load(app):
        """Rediriger la racine / vers /welcome"""
        @app.route('/')
        def index_redirect():
            return redirect('/welcome')


# ============================================================================
# FICHIER 2: plugins/keycloak_auth_manager.py (MODIFICATIONS A FAIRE)
# Ton Auth Manager custom avec les overrides pour autoriser /welcome
# ============================================================================

"""
EXEMPLE DE TON AUTH MANAGER AVEC LES MODIFICATIONS NECESSAIRES

Si tu as deja un fichier keycloak_auth_manager.py, ajoute ces methodes.
Sinon, voici un exemple complet :
"""

# from airflow.auth.managers.fab.fab_auth_manager import FabAuthManager
# from flask import request
# from flask_login import current_user

# class KeycloakAuthManager(FabAuthManager):
#     """
#     Custom Auth Manager pour Keycloak OIDC avec support de page publique /welcome
#     """
#     
#     def is_logged_in(self) -> bool:
#         """
#         Override pour permettre l'acces a certaines routes publiques sans login
#         """
#         # Routes publiques accessibles sans authentification
#         public_paths = ['/welcome', '/', '/static']
#         
#         # Verifier si on est sur une route publique
#         if any(request.path.startswith(path) for path in public_paths):
#             return True
#         
#         # Pour toutes les autres routes, verifier l'authentification normalement
#         return super().is_logged_in()
#     
#     def is_authorized_view(self, access_view: str) -> bool:
#         """
#         Autoriser certaines vues sans authentification
#         """
#         # Vues publiques
#         public_views = [
#             'welcome_page.welcome',
#             'index_redirect',
#             'Airflow.login',
#             'AuthOAuthView.oauth_authorized'
#         ]
#         
#         if access_view in public_views:
#             return True
#         
#         return super().is_authorized_view(access_view)
#     
#     # Le reste de ton implementation Keycloak reste inchange
#     # (get_url_login, oauth config, etc.)


# ============================================================================
# FICHIER 3: Configuration Astronomer values.yaml
# ============================================================================

"""
Dans ton values.yaml Astronomer, ajoute:

airflow:
  config:
    AIRFLOW__CORE__AUTH_MANAGER: plugins.keycloak_auth_manager.KeycloakAuthManager
    AIRFLOW__WEBSERVER__UPDATE_FAB_PERMS: "True"
  
  extraEnv:
    - name: VERSION
      value: "3.1.4"
"""


# ============================================================================
# DEPLOIEMENT SUR ASTRONOMER
# ============================================================================

"""
Structure de ton projet:

your-airflow-project/
âââ dags/
âââ plugins/
â   âââ welcome_page_plugin.py          # Le plugin avec la page welcome
â   âââ keycloak_auth_manager.py        # Ton Auth Manager modifie
âââ Dockerfile
âââ packages.txt
âââ requirements.txt
âââ airflow_settings.yaml (optionnel)

Ensuite:
1. astro dev restart (en local)
2. astro deploy (en production)
"""


# ============================================================================
# FLOW D'AUTHENTIFICATION
# ============================================================================

"""
AVANT (sans welcome page):
User accede a https://airflow.example.com
  â Redirige vers Keycloak
  â Login Keycloak
  â Retour sur /home Airflow

APRES (avec welcome page):
User accede a https://airflow.example.com
  â Redirige vers /welcome (PAGE PUBLIQUE)
  â User clique "Se connecter"
  â Redirige vers /login qui declenche Keycloak OIDC
  â Login Keycloak
  â Retour sur /home Airflow
"""


# ============================================================================
# NOTES IMPORTANTES
# ============================================================================

"""
1. La route /welcome DOIT etre publique (pas d'auth requise)
2. Le bouton "Se connecter" pointe vers /login (route Airflow standard)
3. /login declenche automatiquement ton flow Keycloak OIDC
4. Apres auth reussie, Keycloak redirige vers /home
5. La version est recuperee depuis os.environ.get('VERSION')

DEPANNAGE:
- Si tu vois un 401 Unauthorized sur /welcome:
  â Verifie que is_logged_in() retourne True pour /welcome
  â Verifie que ton Auth Manager est bien charge
  
- Si la redirection / vers /welcome ne fonctionne pas:
  â Verifie les logs du webserver
  â Assure-toi que le plugin est bien dans le dossier plugins/

- Pour tester en local:
  astro dev logs -f webserver
  Puis accede a http://localhost:8080
"""
