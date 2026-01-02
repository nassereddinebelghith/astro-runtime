# Airflow 3.1.4 Architecture with Pre-Login and Keycloak OIDC Authentication

## 📋 Overview

This architecture implements a custom authentication system for **Apache Airflow 3.1.4** deployed on **Kubernetes** with **Astronomer Runtime**, using **Keycloak OIDC** for authentication and a **custom pre-login system** to work around UI customization limitations in Airflow 3.x.

---

## 🎯 Migration Context

### Why This Architecture?

**Problem Identified:**
- ❌ Airflow 3.x no longer allows UI customization via `webserver.py`
- ❌ The default login page can no longer be easily branded
- ✅ Solution: Add a pre-login layer with Nginx serving a custom static HTML page

**Versions:**
- 📦 **Before:** Airflow 2.9.3 (Astronomer Runtime)
- 📦 **After:** Airflow 3.1.4 (Astronomer Runtime)

---

## 🏗️ Architecture Components

### 1️⃣ **Pre-Login Layer** (New Component)

#### Kubernetes Service: `prelogin-81554669`
- **Type:** ClusterIP
- **Port:** 8080/TCP
- **Role:** Initial entry point for all users

#### Pre-Login Pod
**Container: Nginx**
- **Image:** Custom nginx image
- **Port:** 8080
- **Configuration:**
  ```nginx
  # Serve branding page
  location = / {
      root /usr/share/nginx/html;
      try_files /index.html =404;
  }
  
  # Serve static assets
  location /assets/ {
      root /usr/share/nginx/html;
      expires 1d;
  }
  
  # Proxy all other requests to Airflow
  location / {
      proxy_pass https://web-81554669:8080;
      # ... proxy headers
  }
  ```

**Mounted Files:**
- `index.html`: Static HTML branding page (logo, custom text)
- `nginx.conf`: Routing configuration

---

### 2️⃣ **Airflow Webserver Layer**

#### Kubernetes Service: `web-81554669`
- **Type:** ClusterIP  
- **Port:** 8080/TCP
- **Role:** Airflow web server with OIDC authentication

#### Airflow Webserver Pod
**Container: Airflow Webserver**
- **Version:** 3.1.4 (Astronomer Runtime)
- **Auth Manager:** Custom Keycloak OIDC Auth Manager

**Internal Components:**

1. **Custom Auth Manager**
   - Implements OIDC protocol
   - Handles OAuth2 Authorization Code flow
   - Communicates with Keycloak

2. **Role Mapper**
   - Reads `ROLE_MAPPING` environment variable
   - Maps Keycloak groups → Airflow roles
   - Supported roles:
     - `Admin`: Full access
     - `Op`: Operator (can trigger/modify DAGs)
     - `Viewer`: Read-only access
     - `User`: Standard user access
     - `Public`: Minimal access

---

### 3️⃣ **Keycloak OIDC Provider**

#### Keycloak Server
- **Protocol:** OpenID Connect (OIDC)
- **Role:** Identity Provider (IdP)
- **Features:**
  - User authentication
  - Group management (LDAP sync possible)
  - JWT token issuance

#### Token Structure
JWT tokens contain:
- `sub`: Unique user identifier
- `email`: User email
- `name`: Full name
- `groups`: List of groups the user belongs to

---

## 🔄 Detailed Authentication Flow

### Phase 1: Initial Access (Pre-Login)

```
1️⃣ User → Browser
   └─ Access: https://astronomer-ap43877-dev-81554669.data.cloud.net.intra

2️⃣ Browser → Ingress Controller
   └─ GET /

3️⃣ Ingress → Service prelogin-81554669
   └─ Route to pre-login service

4️⃣ Service → Nginx Pod
   └─ Forward request to Nginx container

5️⃣ Nginx → index.html
   └─ Serve static HTML branding page

6️⃣ HTML Page → Browser
   └─ Display: Logo, welcome message, "Login" button
```

### Phase 2: Authentication Initiation

```
7️⃣ User clicks "Login"
   └─ Redirect to /login or /home

8️⃣ Nginx detects non-static request
   └─ Proxy to https://web-81554669:8080

9️⃣ Airflow Webserver receives request
   └─ Detect: no active session

🔟 Custom Auth Manager triggers OIDC flow
   └─ Generate Keycloak authorization URL
   └─ Redirect 302 to Keycloak
```

### Phase 3: Keycloak Authentication

```
1️⃣1️⃣ Browser → Keycloak
   └─ GET /oauth/authorize?client_id=...&redirect_uri=...&state=...

1️⃣2️⃣ Keycloak displays login page
   └─ Form: username + password

1️⃣3️⃣ User submits credentials
   └─ Keycloak validates against LDAP/internal database

1️⃣4️⃣ Keycloak generates authorization code
   └─ Redirect to: /oauth-authorized?code=ABC123&state=...
```

### Phase 4: Token Exchange

```
1️⃣5️⃣ Custom Auth Manager receives callback
   └─ Extract authorization code

1️⃣6️⃣ Auth Manager → Keycloak Token Endpoint
   └─ POST /oauth/token
   └─ Body: code=ABC123&grant_type=authorization_code

1️⃣7️⃣ Keycloak returns tokens
   └─ access_token: JWT with user claims
   └─ id_token: JWT with identity information
   └─ refresh_token: To renew tokens
```

### Phase 5: Role Mapping

```
1️⃣8️⃣ Auth Manager decodes JWT
   └─ Extract: email, name, groups[]

1️⃣9️⃣ Role Mapper reads ROLE_MAPPING
   └─ Example:
       {
         "airflow-admins": "Admin",
         "airflow-operators": "Op",
         "airflow-viewers": "Viewer"
       }

2️⃣0️⃣ For each group in groups[]:
   └─ If group exists in ROLE_MAPPING
      └─ Assign corresponding Airflow role
   
   Example:
   User groups = ["airflow-operators", "data-team"]
   → Final role = "Op"
```

### Phase 6: Session Creation and Access

```
2️⃣1️⃣ Airflow creates Flask session
   └─ Store: user_id, email, role, groups
   └─ Set-Cookie: session_id=...

2️⃣2️⃣ Redirect to /home
   └─ User sees Airflow interface
   └─ Menu/features filtered by role
```

---

## 🔐 Role-Based Permissions

| Role | Airflow Permissions |
|------|---------------------|
| **Admin** | All permissions (user management, config, connections, variables, DAGs) |
| **Op** | Can create/modify/execute DAGs, view logs, manage runs |
| **Viewer** | Read-only access to all DAGs and executions |
| **User** | Can view and execute assigned DAGs |
| **Public** | Minimal access, can view public DAG list |

---

## 📝 Technical Configuration

### Required Environment Variables

```bash
# Keycloak OIDC
KEYCLOAK_URL=https://keycloak.example.com
KEYCLOAK_REALM=my-realm
KEYCLOAK_CLIENT_ID=airflow-client
KEYCLOAK_CLIENT_SECRET=***********

# OAuth Redirect
OAUTH_REDIRECT_URI=https://astronomer-ap43877-dev-81554669.data.cloud.net.intra/oauth-authorized

# Role Mapping (JSON)
ROLE_MAPPING='{
  "airflow-admins": "Admin",
  "airflow-ops": "Op",
  "airflow-viewers": "Viewer",
  "data-users": "User"
}'
```

### Nginx ConfigMap Structure

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: astronomer-ap43877-dev-81554669-prelogin
data:
  nginx.conf: |
    # Main configuration
  default.conf: |
    # Server configuration
  index.html: |
    # HTML branding page
```

---

## 🚀 Architecture Benefits

### ✅ Advantages

1. **Custom Branding**: Fully customizable landing page
2. **Separation of Concerns**: Pre-login decoupled from Airflow
3. **Enhanced Security**: Centralized authentication via Keycloak
4. **Flexible Role Management**: Configurable mapping via environment variable
5. **Airflow 3.x Compatibility**: Works around customization limitations

### ⚠️ Considerations

1. **Double Proxy Layer**: Nginx → Airflow (slight latency)
2. **Maintenance**: Two services to maintain (prelogin + webserver)
3. **Token Expiration**: Requires refresh token management
4. **Configuration**: ROLE_MAPPING must be kept up to date

---

## 🔧 Troubleshooting

### Problem: 503 Service Unavailable

**Possible Causes:**
1. ❌ Ingress points to wrong service
2. ❌ Service selector doesn't match pod labels
3. ❌ ReadinessProbe fails (probe to unavailable Airflow)

**Solution:**
```bash
# Check ingress
kubectl get ingress -n astronomer-ap43877-dev-81554669

# Check endpoints
kubectl get endpoints prelogin-81554669 -n astronomer-ap43877-dev-81554669

# Test nginx locally
kubectl exec -it deployment/prelogin -n astronomer-ap43877-dev-81554669 -- curl http://localhost:8080/
```

### Problem: OAuth Redirect Loop

**Cause:** Incorrect redirect_uri configuration in Keycloak

**Solution:**
- Verify `redirect_uri` in Keycloak client config includes `/oauth-authorized`
- Check `OAUTH_REDIRECT_URI` variable in Airflow deployment

---

## 📊 Metrics and Monitoring

### Recommended Measurement Points

1. **Nginx (prelogin)**
   - Requests/second
   - Response latency
   - 5xx errors

2. **Airflow Webserver**
   - Active sessions
   - Successful/failed authentications
   - OAuth flow latency

3. **Keycloak**
   - Token endpoint response time
   - Authentication failure rate

---

## 📚 References

- [Airflow 3.x Documentation](https://airflow.apache.org/docs/)
- [Keycloak OIDC](https://www.keycloak.org/docs/latest/securing_apps/#_oidc)
- [OAuth 2.0 Authorization Code Flow](https://oauth.net/2/grant-types/authorization-code/)
- [Astronomer Runtime](https://docs.astronomer.io/)

---

**Document Version:** 1.0  
**Date:** December 2025  
**Author:** Nassereddine - Data Engineering Team
