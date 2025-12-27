============================================================
Airflow Authentication Architecture
Keycloak – Custom Auth Manager – Nginx Reverse Proxy
============================================================

1. PURPOSE
----------

This architecture provides a centralized, secure, and enterprise-compliant
authentication mechanism for Apache Airflow.

It is designed to:
- keep authentication logic entirely inside Airflow
- rely on Keycloak as the Identity Provider (IdP)
- enforce authorization based on LDAP groups
- maintain a clear separation of responsibilities between components

The system is intentionally simple at the proxy level and strict at the
application level.


2. ARCHITECTURE OVERVIEW
------------------------

The architecture is composed of clearly separated layers to ensure:
- security
- maintainability
- auditability
- compliance with OAuth/OIDC best practices

The main layers are:
1. User Browser
2. Nginx Reverse Proxy
3. Apache Airflow
4. Custom Keycloak Auth Manager
5. Keycloak Identity Provider
6. LDAP Directory


3. USER AUTHENTICATION FLOW
---------------------------

Step 1 – Initial Access
- The user accesses the exposed URL.
- Nginx acts purely as an HTTP reverse proxy.
- A simple landing page may be displayed.
- No authentication is triggered at this stage.

Step 2 – User Action
- The user clicks on “Sign in with Keycloak”.
- Traffic is forwarded by Nginx to Apache Airflow.

Step 3 – Airflow Authentication Trigger
- Apache Airflow takes control.
- The Custom Keycloak Auth Manager is invoked.
- Airflow initiates the OAuth authentication flow.

Step 4 – Keycloak Authentication
- The user is redirected to Keycloak.
- Keycloak handles authentication (SSO, MFA, certificates, etc.).
- Keycloak queries LDAP to retrieve the user’s group memberships.

Step 5 – Session Creation
- Keycloak generates OAuth/OIDC tokens.
- Tokens are sent back to Airflow.
- An authenticated session is created on the Airflow side.

Step 6 – Authorization
- The Custom Keycloak Auth Manager extracts LDAP groups from the tokens.
- Groups are mapped to Airflow roles.
- Access is granted according to the user’s LDAP group membership.


4. COMPONENT RESPONSIBILITIES
-----------------------------

User Browser
- Entry point for the user
- Contains no authentication logic
- Holds only session cookies

Nginx Reverse Proxy
- Acts strictly as a reverse proxy
- Routes traffic to Airflow
- May serve a static landing page
- Does NOT:
  - authenticate users
  - manage sessions
  - trigger OAuth
  - inspect tokens

Apache Airflow
- Core application
- Manages user sessions
- Enforces RBAC
- Delegates authentication logic to the Custom Auth Manager

Custom Keycloak Auth Manager
- Custom extension of Airflow
- Single source of truth for authentication and authorization
- Responsible for:
  - Keycloak integration
  - token validation
  - token refresh and expiration handling
  - LDAP group extraction
  - mapping groups to Airflow roles

Keycloak
- Identity Provider (IdP)
- Handles authentication and SSO
- Issues OAuth/OIDC tokens
- Integrates with LDAP

LDAP
- Central identity and group directory
- Source of authorization information
- Queried only by Keycloak


5. SECURITY PRINCIPLES
---------------------

- Authentication is handled exclusively by Airflow
- No authentication or authorization logic exists in Nginx
- No public or anonymous Airflow roles are assigned
- OAuth/OIDC flow is strictly enforced
- Authorization is based on LDAP group membership
- No security decisions are delegated to the proxy layer

This design avoids common anti-patterns such as:
- proxy-driven authentication
- role assignment before identity validation
- token handling outside the application layer


6. SOURCE CODE MANAGEMENT
-------------------------

- All custom components (including the Custom Auth Manager) are versioned
  in GitLab.
- Changes are traceable and auditable.
- The architecture is CI/CD agnostic and independent of the deployment method.


7. ARCHITECTURE DIAGRAM (LOGICAL)
---------------------------------

User Browser
     |
     | HTTPS
     v
Nginx Reverse Proxy
     |
     | ProxyPass
     v
Apache Airflow
     |
     | Custom Keycloak Auth Manager
     v
Keycloak Identity Provider
     |
     | LDAP
     v
LDAP Directory


8. CONCLUSION
-------------

This architecture:
- follows OAuth/OIDC best practices
- enforces strict separation of responsibilities
- avoids security shortcuts
- is suitable for enterprise and regulated environments
- is resilient to Airflow version upgrades

Authentication is intentionally centralized in Airflow, while the proxy layer
remains simple and transparent.

This design provides a robust, maintainable, and audit-friendly foundation
for secure Airflow deployments.