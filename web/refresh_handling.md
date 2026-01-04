# Token Expiration Handling in Airflow 3.1.5 with Custom Keycloak OIDC Auth Manager

**Author:** Nassereddine - Data Engineering Team  
**Date:** December 2025  
**Version:** 1.0  
**Airflow Version:** 3.1.5 (Astronomer Runtime)  
**Keycloak Provider:** 0.4.1

-----

## 📋 Table of Contents

1. [Executive Summary](#executive-summary)
1. [Context & Background](#context--background)
1. [Problem Statement](#problem-statement)
1. [Solution Overview](#solution-overview)
1. [Implementation Details](#implementation-details)
1. [Testing & Validation](#testing--validation)
1. [Deployment Guide](#deployment-guide)
1. [References](#references)

-----

## 📋 Executive Summary

This document describes the implementation of automatic token refresh and session expiration handling for Apache Airflow 3.1.5 using a **custom Keycloak OIDC Auth Manager** based on the official Keycloak provider 0.4.1.

### Why a Custom Auth Manager?

The standard Keycloak Auth Manager (`apache-airflow-providers-keycloak==0.4.1`) does **not** support:

- ❌ Custom role mapping from Keycloak groups to Airflow roles
- ❌ Enterprise LDAP group synchronization with Airflow permissions
- ❌ Fine-grained role-based access control (RBAC) customization
- ❌ Dynamic role assignment based on organizational structure

**Our custom implementation extends the base Keycloak Auth Manager to:**

- ✅ Map LDAP/Keycloak groups to Airflow roles via `ROLE_MAPPING` environment variable
- ✅ Support roles: Admin, Op (Operator), Viewer, User, Public
- ✅ Extract groups from Keycloak JWT tokens and map them to Airflow permissions
- ✅ Maintain compatibility with Airflow 3.x’s new Auth Manager architecture

**Example Role Mapping:**

```python
ROLE_MAPPING = {
    "airflow-admins": "Admin",        # Full access
    "airflow-operators": "Op",        # Can trigger/modify DAGs
    "airflow-viewers": "Viewer",      # Read-only access
    "data-users": "User"              # Standard user access
}
```

### Why Manual Token Refresh Handling?

As of **Keycloak provider 0.4.1** and **Airflow 3.1.5**, token refresh handling is **NOT fully implemented** in the base provider, leading to critical user experience issues.

**GitHub Evidence:**

1. **Issue #59277** - [Keycloak provider gives 500 on expired access token](https://github.com/apache/airflow/issues/59277)
- Reported: 3 weeks ago
- Status: Closed
- Description: “When making a request to the Airflow API using a valid Airflow token that contains an expired access token from Keycloak, I receive a 500 internal server error instead of 401/403”
1. **PR #59281** - [Fix 403 when the Keycloak access token is expired](https://github.com/apache/airflow/pull/59281)
- Merged into: apache:main
- Files changed: `keycloak_auth_manager.py`
- Key fix: Added handling for HTTP 401 response code in `_is_authorized()` method

**Problems Without Custom Token Handling:**

- 🔴 500 Internal Server Error when tokens expire → **Server crashes, bad UX**
- 🔴 Users stuck on blank pages → **Must manually clear cookies**
- 🔴 403 Forbidden instead of re-authentication → **Access denied without reason**
- 🔴 No automatic token refresh → **Frequent re-logins required**
- 🔴 “Working outside of request context” errors → **Background tasks fail**

**Our Solution:**
We implemented the fixes from PR #59281 **plus additional enhancements** for our custom auth manager to ensure:

- ✅ Seamless token refresh in background
- ✅ Graceful handling of all error scenarios (401, 400, 403)
- ✅ Automatic redirect to login when re-authentication needed
- ✅ Proper session cleanup
- ✅ No server errors, only clean redirects

-----

## 🎯 Context & Background

### Architecture Overview

Our Airflow deployment uses a **custom pre-login layer** due to limitations in Airflow 3.x:

```
┌─────────────────────────────────────────────────────────────┐
│  User Browser                                                │
└────────────────┬────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│  Ingress Controller                                          │
│  https://airflow.example.com                                 │
└────────────────┬────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│  Pre-Login Service (Nginx)                                   │
│  • Serves custom branded landing page                        │
│  • Proxies authenticated requests to Airflow                 │
│  Reason: Airflow 3.x removed webserver.py customization     │
└────────────────┬────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│  Airflow Webserver (with Custom Keycloak Auth Manager)      │
│  • Custom Auth Manager extends KeycloakAuthManager           │
│  • Implements custom role mapping                            │
│  • Handles token refresh (our implementation)                │
└────────────────┬────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│  Keycloak OIDC Provider                                      │
│  • Issues access_token (short-lived, ~5 min)                 │
│  • Issues refresh_token (long-lived, ~30 min)                │
│  • Syncs with enterprise LDAP                                │
└─────────────────────────────────────────────────────────────┘
```

### Why We Need a Custom Auth Manager

**Standard Keycloak Auth Manager Code (Base Provider):**

```python
# apache-airflow-providers-keycloak/auth_manager/keycloak_auth_manager.py
class KeycloakAuthManager(BaseAuthManager):
    def _extract_roles_from_token(self, access_token: str) -> list:
        # ❌ Only extracts roles from token
        # ❌ No custom mapping logic
        decoded = jwt.decode(access_token, ...)
        return decoded.get('roles', [])
```

**Our Custom Auth Manager Code:**

```python
class OldcKeycloakAuthManager(KeycloakAuthManager):
    def _extract_roles_from_token(self, access_token: str) -> list:
        # ✅ Extracts groups from token
        decoded = jwt.decode(access_token, options={"verify_signature": False})
        groups = decoded.get('groups', [])
        
        # ✅ Maps groups to Airflow roles using ROLE_MAPPING
        role_mapping = json.loads(os.getenv('ROLE_MAPPING', '{}'))
        
        roles = []
        for group in groups:
            if group in role_mapping:
                airflow_role = role_mapping[group]
                roles.append(airflow_role)
        
        return roles
```

**Key Difference:** We map Keycloak/LDAP groups → Airflow roles dynamically.

-----

## 🔴 Problem Statement

### Original Issues (Before Our Fix)

When using the base Keycloak Auth Manager with Airflow 3.1.5, users experienced critical issues:

#### 1. Token Expiration Causes 500 Error

**Scenario:**

- User logs in successfully
- Access token expires after 5 minutes (Keycloak default)
- User clicks on any Airflow page

**Expected Behavior:**

- Token should refresh automatically in background
- OR user should be redirected to login if refresh fails

**Actual Behavior (BEFORE FIX):**

```
[ERROR] Internal Server Error: 500
KeycloakPostError: Token is not active
```

**Impact:** ❌ Server crashes, terrible user experience

#### 2. Users Stuck on Blank Pages

**Scenario:**

- Refresh token expires or becomes invalid (after Keycloak logout)
- User tries to navigate in Airflow

**Actual Behavior (BEFORE FIX):**

- Blank white page
- No error message
- No redirect to login
- User must manually clear cookies to recover

**Impact:** ❌ Users unable to access Airflow, must contact support

#### 3. 403 Forbidden Instead of Re-authentication

**Scenario:**

- Token refresh fails (refresh token invalid)
- `refresh_user()` returns `None`

**Actual Behavior (BEFORE FIX):**

```
HTTP 403 Forbidden
You don't have permission to access this resource
```

**Why This Happens:**

```python
# In _is_authorized()
if not refreshed:
    return False  # ← Airflow interprets this as "user has no permission"
                  #   Instead of "user needs to re-authenticate"
```

**Impact:** ❌ Confusing error message, users think they lost permissions

#### 4. “Working Outside of Request Context” Error

**Scenario:**

- Airflow scheduler or background task calls `refresh_user()`
- Code tries to access Flask `session` object

**Actual Behavior (BEFORE FIX):**

```python
RuntimeError: Working outside of request context.

This typically means that you attempted to use functionality that needed
an active HTTP request. Consult the documentation on testing for
information about how to avoid this problem.
```

**Why This Happens:**

```python
# In refresh_user()
from flask import session
session.clear()  # ← Crashes if not in HTTP request context
```

**Impact:** ❌ Background tasks crash, scheduler becomes unstable

### Root Causes

After analyzing the codebase and GitHub issues, we identified three root causes:

#### Root Cause #1: Missing Token Refresh Logic

```python
# Base Keycloak Auth Manager - MISSING refresh logic
def refresh_user(self, *, user):
    # ❌ No implementation
    pass
```

The base provider does not implement automatic token refresh.

#### Root Cause #2: Incorrect Exception Handling

```python
# Original problematic code
except KeycloakPostError as exc:
    if "invalid_grant" in str(exc):
        # ❌ Raising HTTPException doesn't trigger auth flow
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired"
        )
```

**Problem:** `HTTPException` bubbles up as a 500 error instead of triggering re-authentication.

**Correct Approach:** Return `None` to signal Airflow that user is not authenticated.

#### Root Cause #3: No Context Checking

```python
# Dangerous code - crashes outside HTTP request
from flask import session
session.clear()  # ❌ Fails in background tasks
```

**Solution:** Check if we’re in an HTTP request context first:

```python
from flask import has_request_context, session

if has_request_context():
    session.clear()  # ✅ Safe
```

-----

## ✅ Solution Overview

### Solution Architecture

We implemented a **three-layer defense** against token expiration:

```
Layer 1: AUTOMATIC TOKEN REFRESH
─────────────────────────────────
refresh_user() method
├─ Checks if access_token is expired
├─ Calls Keycloak token endpoint with refresh_token
├─ Updates user object with new tokens
└─ Updates Flask session (if in request context)

Result: User stays logged in seamlessly


Layer 2: GRACEFUL FAILURE HANDLING
───────────────────────────────────
refresh_user() method (error handling)
├─ Catches KeycloakPostError exceptions
├─ Handles 401, 400, 403 error codes
├─ Clears Flask session (if in request context)
└─ Returns None (signals: user not authenticated)

Result: No server crashes, clean failure


Layer 3: FORCED RE-AUTHENTICATION
──────────────────────────────────
_is_authorized() method
├─ Detects refresh failure (refresh_user returned None)
├─ Clears session completely
├─ Raises custom RedirectToLogin exception
└─ User browser receives HTTP 302 redirect

Result: User automatically redirected to login page
```

### Flow Diagram

```
┌──────────────────────────────────────────────────────┐
│  User makes request to Airflow                       │
└────────────────┬─────────────────────────────────────┘
                 │
                 ▼
┌──────────────────────────────────────────────────────┐
│  _is_authorized() called                             │
│  Check: Is access_token expired?                     │
└────────────────┬─────────────────────────────────────┘
                 │
        ┌────────┴────────┐
        │                 │
       No                Yes
        │                 │
        │                 ▼
        │    ┌──────────────────────────────────┐
        │    │  Call refresh_user()              │
        │    └────────────┬─────────────────────┘
        │                 │
        │        ┌────────┴────────┐
        │       Success           Failure
        │        │                 │
        │        ▼                 ▼
        │    ┌─────────┐      ┌──────────────┐
        │    │ Update  │      │ Return None  │
        │    │ user    │      └──────┬───────┘
        │    └────┬────┘             │
        │         │                  │
        │         │                  ▼
        │         │      ┌──────────────────────┐
        │         │      │ Clear session        │
        │         │      │ Raise RedirectToLogin│
        │         │      └──────┬───────────────┘
        │         │             │
        │         │             ▼
        │         │      ┌──────────────────────┐
        │         │      │ HTTP 302 Redirect    │
        │         │      │ → /login             │
        │         │      └──────┬───────────────┘
        │         │             │
        │         │             ▼
        │         │      ┌──────────────────────┐
        │         │      │ Pre-login page shown │
        │         │      │ User clicks "Login"  │
        │         │      └──────┬───────────────┘
        │         │             │
        │         │             ▼
        │         │      ┌──────────────────────┐
        │         │      │ Redirect to Keycloak │
        │         │      │ OAuth flow initiated │
        │         │      └──────┬───────────────┘
        │         │             │
        │         │             ▼
        │         │      ┌──────────────────────┐
        │         │      │ User authenticates   │
        │         │      │ New tokens obtained  │
        │         │      └──────┬───────────────┘
        │         │             │
        └─────────┴─────────────┴────────────────┐
                                                  │
                                                  ▼
                                    ┌──────────────────────┐
                                    │ Continue with        │
                                    │ authorization check  │
                                    └──────────────────────┘
```

-----

## 🔧 Implementation Details

### Method 1: `refresh_user()` - Automatic Token Refresh

**Purpose:** Automatically refresh expired access tokens using refresh tokens.

**Location:** `custom_kc_auth_plugin.py` (Custom Auth Manager)

**Complete Implementation:**

```python
def refresh_user(self, *, user: KeycloakAuthManagerUser) -> KeycloakAuthManagerUser | None:
    """
    Refresh user authentication on each request.
    Handles token expiration and automatic refresh.
    
    This method is called by Airflow on EVERY HTTP request to validate the user.
    
    Returns:
        - User object if token is valid or successfully refreshed
        - None if token expired and refresh failed (triggers redirect to login)
    
    Error Handling:
        - 401 Unauthorized: Token expired and cannot be refreshed
        - 400 + invalid_grant: Refresh token is invalid/expired
        - 403 Forbidden: User doesn't have permission
        - Any other error: Unexpected Keycloak error
    """
    from flask import has_request_context, session
    import logging
    
    logger = logging.getLogger(__name__)
    
    try:
        # ═══════════════════════════════════════════════════════════
        # STEP 1: Check if token is still valid
        # ═══════════════════════════════════════════════════════════
        if not self._token_expired(user.access_token):
            # Token is still valid, no refresh needed
            return user

        # ═══════════════════════════════════════════════════════════
        # STEP 2: Token expired, attempt refresh
        # ═══════════════════════════════════════════════════════════
        logger.debug(f"Token expired for user {user.email}, attempting refresh")
        
        client = self.get_keycloak_client()
        tokens = client.refresh_token(user.refresh_token)
        
        # Keycloak returns:
        # {
        #   "access_token": "eyJ...",      # New access token
        #   "refresh_token": "eyJ...",     # New refresh token
        #   "expires_in": 300,             # Seconds until expiration
        #   "refresh_expires_in": 1800,    # Seconds until refresh expires
        #   "token_type": "Bearer"
        # }

        # ═══════════════════════════════════════════════════════════
        # STEP 3: Update user object with new tokens
        # ═══════════════════════════════════════════════════════════
        user.refresh_token = tokens["refresh_token"]
        user.access_token = tokens["access_token"]
        
        # ═══════════════════════════════════════════════════════════
        # STEP 4: Update Flask session (ONLY if in request context)
        # ═══════════════════════════════════════════════════════════
        # CRITICAL: Background tasks (scheduler, workers) call this method
        # outside of HTTP request context. We must check before accessing
        # Flask's session object to prevent "working outside request context" error.
        
        if has_request_context():
            session['access_token'] = tokens["access_token"]
            session['refresh_token'] = tokens["refresh_token"]
            session.modified = True  # Force session update
        
        logger.info(f"Token refreshed successfully for user {user.email}")
        return user

    # ═══════════════════════════════════════════════════════════════
    # ERROR HANDLING: Keycloak errors during refresh
    # ═══════════════════════════════════════════════════════════════
    except KeycloakPostError as exc:
        msg = str(exc)
        
        # ───────────────────────────────────────────────────────────
        # Handle 401 Unauthorized - Token expired, cannot refresh
        # ───────────────────────────────────────────────────────────
        # Keycloak returns 401 when:
        # - Access token is expired AND
        # - Refresh token is also expired OR
        # - User session was invalidated server-side
        
        if getattr(exc, "response_code", None) == 401:
            if has_request_context():
                session.clear()  # Remove all authentication data
            
            logger.info(f"Received 401 from Keycloak: {msg}. Token expired, session cleared.")
            
            # Return None = "user not authenticated"
            # Airflow will trigger re-authentication flow
            return None
        
        # ───────────────────────────────────────────────────────────
        # Handle 400 + invalid_grant - Refresh token invalid
        # ───────────────────────────────────────────────────────────
        # Keycloak returns:
        # HTTP 400: {"error":"invalid_grant","error_description":"Token is not active"}
        #
        # This happens when:
        # - Refresh token expired
        # - User logged out in Keycloak Admin Console
        # - Keycloak realm settings changed
        # - Refresh token was revoked
        
        if getattr(exc, "response_code", None) == 400 and "invalid_grant" in msg:
            if has_request_context():
                session.clear()
            
            logger.info(f"Token refresh failed (invalid_grant): {msg}. Session cleared.")
            return None
        
        # ───────────────────────────────────────────────────────────
        # Handle 403 Forbidden - User doesn't have permission
        # ───────────────────────────────────────────────────────────
        if getattr(exc, "response_code", None) == 403:
            if has_request_context():
                session.clear()
            
            logger.info(f"Received 403 from Keycloak: {msg}. Session cleared.")
            return None

        # ───────────────────────────────────────────────────────────
        # Any other Keycloak error - Log and fail gracefully
        # ───────────────────────────────────────────────────────────
        logger.error(f"Keycloak error during token refresh: {msg}")
        return None

    # ═══════════════════════════════════════════════════════════════
    # ERROR HANDLING: Unexpected errors
    # ═══════════════════════════════════════════════════════════════
    except Exception as exc:
        # Network errors, parsing errors, etc.
        logger.exception(f"Unexpected error in refresh_user: {exc}")
        return None
```

**Key Implementation Details:**

1. **Context-Aware Session Access**
   
   ```python
   if has_request_context():
       session['access_token'] = new_token
   ```
- Prevents “working outside of request context” error
- Background tasks can safely call this method
1. **Return `None` Instead of Raising Exceptions**
   
   ```python
   return None  # ← Correct
   # vs
   raise HTTPException(401)  # ← Wrong, causes 500 error
   ```
- Follows Airflow’s expected Auth Manager pattern
- `None` = user not authenticated → triggers re-auth
1. **Session Cleanup**
   
   ```python
   session.clear()  # Remove all auth data
   ```
- Prevents stale authentication data
- Security best practice
1. **Comprehensive Error Handling**
- Handles 401, 400, 403 from Keycloak
- Logs detailed information for debugging
- Never crashes, always returns gracefully

### Method 2: `_is_authorized()` - Authorization Check with Redirect

**Purpose:** Check user authorization and handle token expiration gracefully.

**Location:** `custom_kc_auth_plugin.py` (Custom Auth Manager)

**Complete Implementation:**

```python
def _is_authorized(
    self,
    *,
    method,
    resource_type: KeycloakResource,
    user: KeycloakAuthManagerUser,
    resource_id=None,
    attributes=None,
) -> bool:
    """
    Check if user is authorized to access a resource.
    Automatically refreshes token if expired.
    
    This method is called by Airflow before EVERY action to check permissions.
    
    Args:
        method: HTTP method (GET, POST, PUT, DELETE)
        resource_type: Type of resource (DAG, Connection, Variable, etc.)
        user: Current user object with tokens
        resource_id: Optional ID of specific resource
        attributes: Optional additional attributes
    
    Returns:
        True if user is authorized
        False if user is not authorized
    
    Raises:
        RedirectToLogin: Custom HTTP exception that redirects to login page
    """
    from flask import has_request_context, session, redirect, url_for
    from werkzeug.exceptions import HTTPException
    import logging
    
    logger = logging.getLogger(__name__)
    
    # ═══════════════════════════════════════════════════════════════
    # STEP 1: Check if token is expired and refresh if needed
    # ═══════════════════════════════════════════════════════════════
    if self._token_expired(user.access_token):
        logger.debug(f"Token expired in _is_authorized for user {user.email}")
        
        # Attempt refresh
        refreshed = self.refresh_user(user=user)
        
        # ═══════════════════════════════════════════════════════════
        # STEP 2: Handle refresh failure - Force redirect to login
        # ═══════════════════════════════════════════════════════════
        if not refreshed:
            # refresh_user() returned None = refresh failed
            # User needs to re-authenticate
            
            logger.warning(f"Token refresh failed for {user.email}, forcing redirect to login")
            
            if has_request_context():
                # Clear any remaining session data
                session.clear()
                
                # ═══════════════════════════════════════════════════
                # CRITICAL: Custom HTTP Exception for Redirect
                # ═══════════════════════════════════════════════════
                # We create a custom exception that returns HTTP 302 redirect
                # instead of just returning False (which would cause 403)
                
                class RedirectToLogin(HTTPException):
                    """
                    Custom HTTP exception that forces redirect to login page.
                    
                    When raised, Flask will:
                    1. Call get_response() method
                    2. Return the redirect response to the browser
                    3. Browser follows redirect to /login
                    4. OAuth flow is initiated
                    """
                    code = 302
                    description = 'Session expired, redirecting to login'
                    
                    def get_response(self, environ=None):
                        # Return HTTP 302 redirect to login page
                        return redirect(url_for('Airflow.login', _external=True))
                
                # Raise the custom exception
                # This stops execution and redirects the user
                raise RedirectToLogin()
            
            # If not in request context (background task), just return False
            return False
        
        # ═══════════════════════════════════════════════════════════
        # STEP 3: Token refreshed successfully, update user
        # ═══════════════════════════════════════════════════════════
        user = refreshed
    
    # ═══════════════════════════════════════════════════════════════
    # STEP 4: Extract HTTP method for authorization check
    # ═══════════════════════════════════════════════════════════════
    method_str = method.value if hasattr(method, "value") else str(method)
    
    # Special case: GET without resource_id means LIST operation
    # Example: GET /dags → List all DAGs
    if method_str.upper() == "GET" and resource_id is None:
        method_str = "LIST"
    
    # ═══════════════════════════════════════════════════════════════
    # STEP 5: Extract roles and check authorization
    # ═══════════════════════════════════════════════════════════════
    # Extract groups from JWT token
    roles = self._extract_roles_from_token(user.access_token)
    
    # Check if any of user's roles allow this action
    # This uses our custom role mapping logic
    return self._role_allows(roles, resource_type, method_str)
```

**Why the Custom `RedirectToLogin` Exception?**

**Problem with just returning `False`:**

```python
if not refreshed:
    return False  # ❌ Airflow thinks: "User has no permission"
                  #    Result: HTTP 403 Forbidden
```

**Solution with custom exception:**

```python
if not refreshed:
    raise RedirectToLogin()  # ✅ Flask returns: HTTP 302 Redirect
                              #    Result: User redirected to login
```

**How it works:**

1. Exception is raised
1. Flask catches it
1. Calls `get_response()` method
1. Returns HTTP 302 redirect response
1. Browser follows redirect to `/login`
1. OAuth flow starts again

-----

## 📊 Behavior Comparison

### Before Implementation

|Scenario                            |Old Behavior                                 |HTTP Code|User Experience              |
|------------------------------------|---------------------------------------------|---------|-----------------------------|
|Access token expires (refresh works)|500 Internal Server Error                    |500      |❌ Page breaks completely     |
|Access token expires (happy path)   |No automatic refresh                         |N/A      |❌ Must re-login every 5 min  |
|Refresh token expires               |500 Internal Server Error or stuck page      |500      |❌ Must clear cookies manually|
|Keycloak admin logout               |403 Forbidden or stuck page                  |403      |❌ Confusing error message    |
|Background task with expired token  |RuntimeError: Working outside request context|N/A      |❌ Scheduler/tasks crash      |

### After Implementation

|Scenario                            |New Behavior                     |HTTP Code|User Experience                |
|------------------------------------|---------------------------------|---------|-------------------------------|
|Access token expires (refresh works)|Automatic refresh in background  |200      |✅ Seamless, user doesn’t notice|
|Access token expires (happy path)   |Token auto-refreshes every ~4 min|200      |✅ User stays logged in         |
|Refresh token expires               |Automatic redirect to login      |302      |✅ Clean re-authentication flow |
|Keycloak admin logout               |Automatic redirect to login      |302      |✅ User sees branded login page |
|Background task with expired token  |Graceful failure (returns None)  |N/A      |✅ Task handles gracefully      |

### User Experience Flow

**Before (❌ BAD):**

```
User clicks DAG
   ↓
Token expired
   ↓
500 Internal Server Error
   ↓
User sees generic error page
   ↓
User contacts support
   ↓
Support tells user to clear cookies
   ↓
User manually clears cookies
   ↓
User can login again
```

**After (✅ GOOD):**

```
User clicks DAG
   ↓
Token expired
   ↓
Token auto-refreshes (if refresh_token valid)
   ↓
DAG page loads normally
   OR
   ↓
If refresh_token also expired:
   ↓
Automatic redirect to login page
   ↓
User sees branded login page
   ↓
User clicks "Login"
   ↓
Redirected to Keycloak
   ↓
User authenticates
   ↓
Redirected back to original DAG page
```

-----

## 🧪 Testing & Validation

### Test Case 1: Automatic Token Refresh (Happy Path)

**Objective:** Verify that access tokens are automatically refreshed without user intervention.

**Setup:**

1. Login to Airflow
1. Capture initial tokens from session:
   
   ```bash
   # In browser DevTools → Application → Cookies
   # Copy session cookie value
   # Decode JWT to see expiration times
   ```
1. Wait for access token to approach expiration (~4-5 minutes)

**Test Steps:**

1. Navigate to any Airflow page (e.g., `/dags`)
1. Observe network tab in browser DevTools
1. Check Airflow webserver logs

**Expected Behavior:**

- Page loads normally without any visible delay
- No redirect to login page
- No error messages

**Verification in Logs:**

```bash
kubectl logs deployment/airflow-webserver --tail=100 | grep -i "token"

# Expected output:
INFO - Token refreshed successfully for user john.doe@company.com [custom_kc_auth_plugin] loc=custom_kc_auth_plugin.py:286
```

**Verification in Browser:**

```javascript
// In browser console
// Decode session cookie to verify new tokens
// access_token exp claim should be ~5 min in future
// refresh_token exp claim should be ~30 min in future
```

**Result:** ✅ PASS - Token auto-refreshes, user stays logged in

-----

### Test Case 2: Expired Refresh Token (Re-authentication Flow)

**Objective:** Verify clean redirect to login when refresh token expires.

**Setup:**

1. Login to Airflow
1. In Keycloak Admin Console:
- Navigate to: Users → [Your User] → Sessions
- Click “Logout” to invalidate all sessions
- This makes the refresh_token invalid

**Test Steps:**

1. Return to Airflow (don’t close the tab)
1. Click on any link (e.g., “DAGs” menu)
1. Observe what happens

**Expected Behavior:**

1. `refresh_user()` attempts to use refresh_token
1. Keycloak returns: `HTTP 400 {"error":"invalid_grant","error_description":"Token is not active"}`
1. Session is cleared
1. User is redirected to `/login`
1. Pre-login HTML page is displayed
1. User clicks “Login” button
1. Redirected to Keycloak login page
1. After authentication, redirected back to Airflow

**Verification in Logs:**

```bash
kubectl logs deployment/airflow-webserver --tail=100 | grep -E "invalid_grant|redirect"

# Expected output:
INFO - Token refresh failed (invalid_grant): Token is not active. Session cleared. [custom_kc_auth_plugin] loc=custom_kc_auth_plugin.py:XXX
WARNING - Token refresh failed for john.doe@company.com, forcing redirect to login [custom_kc_auth_plugin] loc=custom_kc_auth_plugin.py:YYY
```

**Verification in Browser Network Tab:**

```
Status  URL                                          Type
302     /dags                                        redirect
302     /login                                       redirect
200     https://keycloak.company.com/auth/realms/... document
```

**What You Should NOT See:**

- ❌ 500 Internal Server Error
- ❌ 403 Forbidden
- ❌ Blank white page
- ❌ Stack traces in logs

**Result:** ✅ PASS - Clean redirect, no errors

-----

### Test Case 3: Multiple Tabs Open

**Objective:** Verify graceful handling when multiple tabs are open and tokens expire.

**Setup:**

1. Login to Airflow
1. Open 4 browser tabs with different pages:
- Tab 1: `/home`
- Tab 2: `/dags`
- Tab 3: `/datasets`
- Tab 4: `/connections`
1. Invalidate refresh token (Keycloak logout)

**Test Steps:**

1. Click in Tab 1 (navigate somewhere)
1. Observe what happens in all tabs
1. Try clicking in Tab 2, Tab 3, Tab 4

**Expected Behavior:**

- Tab 1: Redirects to login immediately
- Tab 2, 3, 4: Redirect to login on next interaction
- No errors in any tab
- No infinite redirect loops
- All tabs eventually show login page

**Verification:**

- Check browser console in each tab (no JavaScript errors)
- Check Airflow logs (no crashes or exceptions)
- All tabs should redirect cleanly

**Result:** ✅ PASS - All tabs handle expiration gracefully

-----

### Test Case 4: Background Tasks with Expired Tokens

**Objective:** Verify that background tasks don’t crash when tokens expire.

**Setup:**

1. Trigger an Airflow DAG run
1. Let DAG run while tokens are expiring
1. DAG tasks may call `refresh_user()` in background context

**Test Steps:**

1. Monitor scheduler logs
1. Monitor worker logs
1. Check if DAG completes successfully

**Expected Behavior:**

- No “working outside of request context” errors
- Tasks complete normally
- If tasks need authentication, they fail gracefully (not crash)

**Verification in Logs:**

```bash
kubectl logs deployment/airflow-scheduler --tail=200 | grep -i "request context"

# Should NOT see:
# RuntimeError: Working outside of request context
```

**Result:** ✅ PASS - Background tasks work normally

-----

## 🚀 Deployment Guide

### Prerequisites

- Airflow 3.1.5 (Astronomer Runtime)
- Keycloak provider 0.4.1 installed
- Custom Auth Manager class already implemented
- Keycloak server configured with OIDC client

### Step 1: Update Custom Auth Manager Code

**File:** `custom_kc_auth_plugin.py`

Replace the `refresh_user()` and `_is_authorized()` methods with the implementations from this document.

```python
class OldcKeycloakAuthManager(KeycloakAuthManager):
    # ... existing code ...
    
    def refresh_user(self, *, user: KeycloakAuthManagerUser) -> KeycloakAuthManagerUser | None:
        # [Copy complete implementation from above]
        pass
    
    def _is_authorized(
        self,
        *,
        method,
        resource_type: KeycloakResource,
        user: KeycloakAuthManagerUser,
        resource_id=None,
        attributes=None,
    ) -> bool:
        # [Copy complete implementation from above]
        pass
```

### Step 2: Verify Configuration

Ensure the following environment variables are set:

```bash
# Keycloak Configuration
KEYCLOAK_URL=https://keycloak.company.com
KEYCLOAK_REALM=my-realm
KEYCLOAK_CLIENT_ID=airflow-client
KEYCLOAK_CLIENT_SECRET=***********

# OAuth Redirect URI
OAUTH_REDIRECT_URI=https://airflow.company.com/oauth-authorized

# Role Mapping (JSON)
ROLE_MAPPING='{
  "airflow-admins": "Admin",
  "airflow-operators": "Op",
  "airflow-viewers": "Viewer",
  "data-users": "User"
}'
```

### Step 3: Deploy to Kubernetes

```bash
# Update the custom auth manager file in your deployment
kubectl -n <namespace> cp custom_kc_auth_plugin.py <pod>:/opt/airflow/plugins/

# OR rebuild Docker image with updated file

# Restart webserver deployment
kubectl -n <namespace> rollout restart deployment/airflow-webserver

# Monitor rollout
kubectl -n <namespace> rollout status deployment/airflow-webserver
```

### Step 4: Verify Deployment

```bash
# Check webserver logs for startup errors
kubectl -n <namespace> logs deployment/airflow-webserver --tail=50

# Should see:
# INFO - Using auth manager: <class 'custom_kc_auth_plugin.OldcKeycloakAuthManager'>
# INFO - Airflow webserver is starting up...
```

### Step 5: Test in Development/Staging First

**DO NOT deploy directly to production!**

1. Deploy to DEV environment first
1. Run all test cases from “Testing & Validation” section
1. Monitor logs for 24-48 hours
1. Verify no user complaints
1. Then deploy to STAGING
1. Finally deploy to PRODUCTION

### Step 6: Monitor Post-Deployment

**Monitor these metrics for 48 hours:**

```bash
# Count of token refreshes (should be frequent)
kubectl logs deployment/airflow-webserver | grep "Token refreshed successfully" | wc -l

# Count of refresh failures (should be rare, only when users logout)
kubectl logs deployment/airflow-webserver | grep "Token refresh failed" | wc -l

# Count of 500 errors (should be ZERO)
kubectl logs deployment/airflow-webserver | grep "500 Internal Server Error" | wc -l

# Count of 403 errors due to token issues (should be ZERO)
kubectl logs deployment/airflow-webserver | grep -E "403.*token|403.*expired" | wc -l
```

**Expected Results:**

- Token refreshes: HIGH (indicates feature is working)
- Refresh failures: LOW (only when users actually logout)
- 500 errors: ZERO
- Token-related 403 errors: ZERO

### Rollback Plan

If issues occur:

```bash
# Rollback to previous deployment
kubectl -n <namespace> rollout undo deployment/airflow-webserver

# Verify rollback
kubectl -n <namespace> rollout status deployment/airflow-webserver

# Check if issues resolved
kubectl -n <namespace> logs deployment/airflow-webserver --tail=100
```

-----

## 📚 References

### GitHub Issues & Pull Requests

1. **[apache/airflow#59277](https://github.com/apache/airflow/issues/59277)** - Keycloak provider gives 500 on expired access token
- Status: Closed
- Impact: High
- Description: Users experiencing 500 errors instead of proper re-authentication
1. **[apache/airflow#59281](https://github.com/apache/airflow/pull/59281)** - Fix 403 when the Keycloak access token is expired
- Status: Merged
- Files changed: `keycloak_auth_manager.py`
- Key changes: Added handling for HTTP 401 in `_is_authorized()`

### Documentation

- [Airflow 3.x Auth Manager Documentation](https://airflow.apache.org/docs/apache-airflow/stable/security/auth-manager.html)
- [Keycloak OIDC Documentation](https://www.keycloak.org/docs/latest/securing_apps/#_oidc)
- [OAuth 2.0 Authorization Code Flow](https://oauth.net/2/grant-types/authorization-code/)
- [OAuth 2.0 Refresh Token](https://oauth.net/2/grant-types/refresh-token/)

### Related Technologies

- **Apache Airflow:** 3.1.5 (Astronomer Runtime)
- **Keycloak Provider:** apache-airflow-providers-keycloak==0.4.1
- **Keycloak:** 24.1
- **Flask:** 3.x (Airflow dependency)
- **python-keycloak:** 4.x (Keycloak provider dependency)

-----

## 📝 Change Log

|Version|Date         |Author      |Changes              |
|-------|-------------|------------|---------------------|
|1.0    |December 2025|Nassereddine|Initial documentation|

-----

## ✅ Approval & Sign-Off

|Role    |Name        |Date    |Signature|
|--------|------------|--------|---------|
|Author  |Nassereddine|Dec 2025|         |
|Reviewer|            |        |         |
|Approver|            |        |         |

-----

**END OF DOCUMENT**