# Imports pour Illumio/ArgoCD
from .argocd_client import ArgoCDClient, ArgoCDConfig as ArgoCDClientConfig
from .illumio_service import IllumioService


# ========================================
# Endpoints pour les labels Illumio
# ========================================

# Initialisation du client ArgoCD et du service Illumio
argocd_client_config = ArgoCDClientConfig(
    url=config.argocd.url,
    token=config.argocd.token,
    verify_ssl=config.argocd.verify_ssl
)
argocd_client = ArgoCDClient(argocd_client_config)
illumio_service = IllumioService(
    argocd_client=argocd_client,
    tenant_namespace_label=config.argocd.tenant_namespace_label
)

@app.get("/v1/instances/{release_id}/illumio-label")
async def get_instance_illumio_label(
    release_id: str,
    user: Annotated[User, Depends()],
):
    """
    Récupère le label Illumio d'une instance Airflow depuis ArgoCD
    
    Équivalent à l'endpoint Java: GET /v1/tenants/{tenantName}/illumio-label
    """
    # Récupérer l'instance depuis l'inventory
    inst = await inventory.get_by_release_id(release_id)
    if inst is None:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    # Vérifier les droits
    if not user.has_right("datahub-managers", inst.customer.apcode, inst.env):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    # Récupérer le label Illumio depuis ArgoCD
    illumio_label = await illumio_service.get_illumio_label(inst.name)
    
    if illumio_label:
        return {
            "label": illumio_label,
            "message": "FOUND",
            "instance_name": inst.name,
            "release_id": release_id
        }
    else:
        return {
            "label": None,
            "message": "NOT_FOUND",
            "instance_name": inst.name,
            "release_id": release_id
        }


@app.get("/v1/instances/{release_id}/labels")
async def get_instance_all_labels(
    release_id: str,
    user: Annotated[User, Depends()],
):
    """
    Récupère tous les labels disponibles pour une instance
    """
    # Récupérer l'instance
    inst = await inventory.get_by_release_id(release_id)
    if inst is None:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    # Vérifier les droits
    if not user.has_right("datahub-managers", inst.customer.apcode, inst.env):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    # Récupérer le label Illumio
    illumio_label = await illumio_service.get_illumio_label(inst.name)
    
    labels = {}
    if illumio_label:
        labels["illumio-label"] = illumio_label
    
    return {
        "labels": labels,
        "instance_name": inst.name,
        "release_id": release_id
    }
