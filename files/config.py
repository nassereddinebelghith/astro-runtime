class ArgoCDConfig(BaseSettings):
    """Configuration ArgoCD"""
    url: str = Field(
        alias="argocd_url",
        default="https://argocd.example.com"
    )
    token: str = Field(
        alias="argocd_token",
        default=""
    )
    verify_ssl: bool = Field(
        alias="argocd_verify_ssl",
        default=True
    )
    # Labels utilisés pour filtrer les applications
    tenant_namespace_label: str = Field(
        alias="argocd_tenant_namespace_label",
        default="astronomer-tenant-namespace"
    )


class Config(BaseSettings):
    gitlab: GitlabConfig = GitlabConfig()
    instance_name: str = "airflow-cp-local"
    issuer: IssuerConfig = IssuerConfig()
    ldap: LdapConfig = LdapConfig()
    log: LogConfig = LogConfig()
    namespace: str
    no_merge: bool = False
    oidc: OidcConfig = OidcConfig()
    server: ServerConfig = ServerConfig()
    argocd: ArgoCDConfig = ArgoCDConfig()  # ← AJOUTER CETTE LIGNE

config = Config()
