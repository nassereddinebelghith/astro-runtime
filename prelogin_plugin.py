"""
Plugin Airflow pour afficher une page de pre-login custom
avant l'authentification Keycloak
"""
from airflow.plugins_manager import AirflowPlugin
from flask import Blueprint, render_template, redirect, request
from flask_login import current_user

# Créer le Blueprint Flask
prelogin_bp = Blueprint(
    "prelogin",
    __name__,
    template_folder='templates',
    static_folder='static',
)

@prelogin_bp.route('/welcome')
def welcome():
    """
    Page de landing custom avec bouton "Sign in with Keycloak"
    Si l'utilisateur est déjà authentifié, redirige vers /home
    """
    if current_user.is_authenticated:
        return redirect('/home')
    
    return render_template('prelogin.html')

@prelogin_bp.route('/start-auth')
def start_auth():
    """
    Démarre le processus d'authentification Keycloak
    Redirige vers la route /login qui déclenche le CustomKeycloakAuthManager
    """
    # Cette route /login est gérée automatiquement par ton CustomKeycloakAuthManager
    return redirect('/login')

@prelogin_bp.before_app_request
def redirect_root_to_welcome():
    """
    Intercepte les requêtes vers la racine "/" et redirige vers /welcome
    si l'utilisateur n'est pas authentifié
    """
    # Ne rediriger que si :
    # 1. Le path est exactement "/"
    # 2. L'utilisateur n'est pas authentifié
    # 3. Ce n'est pas une requête statique ou API
    if (request.path == '/' and 
        not current_user.is_authenticated and
        not request.path.startswith('/static') and
        not request.path.startswith('/api')):
        return redirect('/welcome')

# Plugin Airflow
class PreLoginPlugin(AirflowPlugin):
    """
    Plugin qui enregistre le Blueprint de pre-login dans Airflow
    """
    name = "prelogin"
    flask_blueprints = [prelogin_bp]
