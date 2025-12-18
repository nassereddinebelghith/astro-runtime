# ============================================================================
# A AJOUTER DANS TON FICHIER KEYCLOAK AUTH MANAGER CUSTOM EXISTANT
# ============================================================================

# IMPORTS NECESSAIRES (ajoute ca en haut de ton fichier si pas deja present)
from flask import request


# ============================================================================
# LES DEUX METHODES A AJOUTER DANS TA CLASSE CUSTOM
# ============================================================================

# Ajoute ces deux methodes dans ta classe qui extend KeycloakAuthManager:

def is_logged_in(self) -> bool:
    """
    Override pour permettre l'acces a /welcome sans authentification
    """
    # Routes publiques accessibles sans login
    public_paths = ['/welcome', '/', '/static', '/health', '/api/v1/health']
    
    # Si on est sur une route publique, autoriser l'acces
    if any(request.path.startswith(path) for path in public_paths):
        return True
    
    # Sinon, utiliser la logique normale de Keycloak
    return super().is_logged_in()


def is_authorized_view(self, access_view: str) -> bool:
    """
    Autoriser certaines vues sans authentification
    """
    # Vues publiques qui ne necessitent pas d'authentification
    public_views = [
        'welcome_page.welcome',           # Notre page welcome
        'index_redirect',                  # Redirection racine
        'Airflow.login',                   # Page de login standard
        'AuthOAuthView.oauth_authorized',  # Callback OAuth Keycloak
        'Airflow.health'                   # Health check
    ]
    
    # Autoriser les vues publiques
    if access_view in public_views:
        return True
    
    # Pour toutes les autres vues, utiliser la logique Keycloak normale
    return super().is_authorized_view(access_view)


# ============================================================================
# EXEMPLE DE TA CLASSE COMPLETE (pour reference)
# ============================================================================

"""
Ta classe devrait ressembler a ca:

from airflow_provider_keycloak.auth_manager import KeycloakAuthManager
from flask import request

class MonKeycloakAuthManagerCustom(KeycloakAuthManager):
    
    def __init__(self, appbuilder):
        super().__init__(appbuilder)
        # Ton code custom existant...
    
    # AJOUTE CES DEUX METHODES ICI:
    
    def is_logged_in(self) -> bool:
        public_paths = ['/welcome', '/', '/static', '/health', '/api/v1/health']
        if any(request.path.startswith(path) for path in public_paths):
            return True
        return super().is_logged_in()
    
    def is_authorized_view(self, access_view: str) -> bool:
        public_views = [
            'welcome_page.welcome',
            'index_redirect',
            'Airflow.login',
            'AuthOAuthView.oauth_authorized',
            'Airflow.health'
        ]
        if access_view in public_views:
            return True
        return super().is_authorized_view(access_view)
    
    # Le reste de tes methodes custom existantes...
    # (mapping des roles, etc.)
"""
