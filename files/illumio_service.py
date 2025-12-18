"""
Service pour récupérer les labels Illumio depuis ArgoCD
Équivalent à la logique dans ArgoCdTenantReader.getTenantIllumioLabel()
"""

import logging
from typing import Optional
from .argocd_client import ArgoCDClient, extract_illumio_label_from_namespace

logger = logging.getLogger(__name__)


class IllumioService:
    """
    Service pour gérer les labels Illumio
    """
    
    def __init__(self, argocd_client: ArgoCDClient, tenant_namespace_label: str):
        self.argocd_client = argocd_client
        self.tenant_namespace_label = tenant_namespace_label
    
    async def get_illumio_label(self, tenant_name: str) -> Optional[str]:
        """
        Récupère le label Illumio pour un tenant donné
        
        Logique identique à ArgoCdTenantReader.getTenantIllumioLabel()
        
        Args:
            tenant_name: Nom du tenant (nom de l'instance Airflow)
        
        Returns:
            Label Illumio ou None si non trouvé
        """
        logger.debug(f"Getting Illumio label for tenant: {tenant_name}")
        
        try:
            # 1. Récupérer les applications ArgoCD pour ce tenant
            applications = await self.argocd_client.list_applications(
                namespace_label=self.tenant_namespace_label,
                tenant_label=f"tenant={tenant_name}"
            )
            
            # 2. Validation : doit avoir exactement 1 application
            if not applications:
                logger.warning(f"No ArgoCD application found for tenant: {tenant_name}")
                return None
            
            if len(applications) > 1:
                logger.warning(
                    f"Multiple ArgoCD applications found for tenant {tenant_name}: {len(applications)}"
                )
                return None
            
            application = applications[0]
            logger.debug(f"Found ArgoCD application: {application.get('metadata', {}).get('name')}")
            
            # 3. Récupérer le namespace Kubernetes managé par cette application
            namespace = await self.argocd_client.get_managed_namespace(
                application=application,
                namespace_name=tenant_name
            )
            
            if not namespace:
                logger.warning(f"Namespace {tenant_name} not found in ArgoCD managed resources")
                return None
            
            # 4. Extraire le label Illumio depuis les conditions du namespace
            illumio_label = extract_illumio_label_from_namespace(namespace)
            
            if illumio_label:
                logger.info(f"Illumio label found for {tenant_name}: {illumio_label}")
            else:
                logger.info(f"No Illumio label found for {tenant_name}")
            
            return illumio_label
            
        except Exception as e:
            logger.error(f"Error getting Illumio label for {tenant_name}: {e}", exc_info=True)
            return None
