"""
Client ArgoCD pour interagir avec l'API ArgoCD
Équivalent à ArgoCdClient.java
"""

import httpx
import logging
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ArgoCDConfig:
    """Configuration ArgoCD"""
    url: str
    token: str
    verify_ssl: bool = True


class ArgoCDClient:
    """
    Client pour interagir avec ArgoCD API
    Équivalent à ArgoCdClient.java
    """
    
    def __init__(self, config: ArgoCDConfig):
        self.url = config.url.rstrip('/')
        self.token = config.token
        self.verify_ssl = config.verify_ssl
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }
    
    async def list_applications(
        self, 
        namespace_label: str,
        tenant_label: str
    ) -> List[Dict[str, Any]]:
        """
        Liste les applications ArgoCD avec les labels spécifiés
        
        Args:
            namespace_label: Label du namespace (ex: "sparkaas-tenant-namespace")
            tenant_label: Label du tenant (ex: "tenant=my-tenant")
        
        Returns:
            Liste des applications ArgoCD
        """
        try:
            # Construire le sélecteur de labels
            selector = f"{namespace_label},{tenant_label}"
            
            async with httpx.AsyncClient(verify=self.verify_ssl, timeout=30.0) as client:
                response = await client.get(
                    f"{self.url}/api/v1/applications",
                    headers=self.headers,
                    params={"selector": selector}
                )
                
                if response.status_code == 200:
                    data = response.json()
                    return data.get("items", [])
                else:
                    logger.error(f"Error listing ArgoCD applications: {response.status_code} - {response.text}")
                    return []
                    
        except Exception as e:
            logger.error(f"Exception while listing ArgoCD applications: {e}")
            return []
    
    async def get_managed_namespace(
        self,
        application: Dict[str, Any],
        namespace_name: str
    ) -> Optional[Dict[str, Any]]:
        """
        Récupère le namespace Kubernetes géré par une application ArgoCD
        
        Args:
            application: Application ArgoCD
            namespace_name: Nom du namespace à récupérer
        
        Returns:
            Namespace Kubernetes ou None
        """
        try:
            app_name = application.get("metadata", {}).get("name")
            
            async with httpx.AsyncClient(verify=self.verify_ssl, timeout=30.0) as client:
                # Récupérer les ressources managées par l'application
                response = await client.get(
                    f"{self.url}/api/v1/applications/{app_name}/managed-resources",
                    headers=self.headers
                )
                
                if response.status_code != 200:
                    logger.error(f"Error getting managed resources: {response.status_code}")
                    return None
                
                resources = response.json()
                
                # Chercher le namespace dans les ressources
                for resource in resources.get("items", []):
                    if (resource.get("kind") == "Namespace" and 
                        resource.get("name") == namespace_name):
                        
                        # Récupérer les détails du namespace
                        namespace_response = await client.get(
                            f"{self.url}/api/v1/applications/{app_name}/resource",
                            headers=self.headers,
                            params={
                                "name": namespace_name,
                                "namespace": "",
                                "resourceName": namespace_name,
                                "version": "v1",
                                "kind": "Namespace",
                                "group": ""
                            }
                        )
                        
                        if namespace_response.status_code == 200:
                            return namespace_response.json()
                
                return None
                
        except Exception as e:
            logger.error(f"Exception while getting managed namespace: {e}")
            return None


def extract_illumio_label_from_namespace(namespace: Dict[str, Any]) -> Optional[str]:
    """
    Extrait le label Illumio depuis les conditions du namespace
    
    Logique équivalente à ArgoCdTenantReader.getTenantIllumioLabel()
    
    Le label Illumio est stocké dans:
    namespace.status.conditions[].reason
    où condition.type == "SFM5ConditionType" 
    et condition.message == "LABELLIZED_FULL"
    
    Args:
        namespace: Namespace Kubernetes
    
    Returns:
        Label Illumio ou None
    """
    try:
        # Récupérer le manifest (la ressource Kubernetes réelle)
        manifest = namespace.get("manifest", {})
        
        # Vérifier le status
        status = manifest.get("status")
        if not status:
            logger.debug("Namespace has no status")
            return None
        
        # Récupérer les conditions
        conditions = status.get("conditions", [])
        if not conditions:
            logger.debug("Namespace has no conditions")
            return None
        
        # Chercher la condition avec le label Illumio
        for condition in conditions:
            condition_type = condition.get("type")
            message = condition.get("message")
            reason = condition.get("reason")
            
            if (condition_type == "SFM5ConditionType" and 
                message == "LABELLIZED_FULL" and 
                reason):
                logger.info(f"Found Illumio label: {reason}")
                return reason
        
        logger.debug("No Illumio label found in namespace conditions")
        return None
        
    except Exception as e:
        logger.error(f"Error extracting Illumio label: {e}")
        return None
