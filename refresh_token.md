# Authentication Session Handling – Airflow 3.1.5 with Keycloak

## Context

This project uses **Apache Airflow 3.1.5** with **Keycloak** as the Identity Provider (OIDC), behind an **Nginx reverse proxy**.

The authentication relies on:
- A JWT stored in a browser cookie (`_token`)
- Role-based authorization derived directly from the JWT claims
- **UMA is disabled**

## Problem Statement

In Airflow 3.1.5, when using the Keycloak Auth Manager with UMA disabled, the authentication flow has **fundamental limitations**:

### 1. No reliable token refresh for UI sessions
- Although Keycloak supports refresh tokens, **Airflow UI does not handle refresh correctly**
- The `refresh_user()` logic is insufficient to:
  - regenerate the Airflow JWT
  - overwrite the browser cookie
  - re-evaluate user roles

### 2. Browser cookie becomes a single source of truth
- Airflow reads user identity and roles **only from the JWT cookie**
- Once issued, this cookie:
  - is not properly rotated
  - is not reliably replaced on refresh
  - can become stale or invalid

### 3. Resulting issues
- Infinite `403 Forbidden` responses
- `invalid_grant` errors from Keycloak
- User roles (admin / operator / user) not re-evaluated
- Users stuck in broken sessions until manual cookie deletion

These issues are **not caused by misconfiguration**, but by **design limitations** in Airflow 3.x.

## Why the Provider Cannot Solve This

The official Keycloak Auth Manager:
- Cannot force the browser to drop or replace cookies
- Cannot reliably refresh UI sessions
- Cannot re-issue a new JWT transparently

This makes **automatic token refresh unsupported and unstable** for UI authentication.

## Chosen Solution: Reverse Proxy–Controlled Session Expiration

Instead of attempting to refresh tokens in Airflow, we **control session lifetime at the reverse proxy level** using Nginx.

### Core idea

- The browser cookie (`_token`) is given a **fixed TTL**
- After expiration, the browser automatically deletes it
- On the next request:
  - Airflow sees no token
  - A clean OAuth authentication flow is triggered
  - A new JWT is issued
  - Roles are recalculated correctly

This approach is:
- Deterministic
- Stateless
- Fully compatible with Airflow
- Transparent to the user

## How the Nginx Solution Works

### 1. Apply a TTL to the authentication cookie
When a `_token` cookie is detected, Nginx re-emits it **once per session** with a controlled lifetime:

- Example TTL: 120 seconds (test)
- Production TTL: typically 30 minutes

### 2. Prevent infinite loops
A secondary marker cookie (`token_timer`) ensures that:
- The TTL is applied only once
- The cookie is not rewritten on every request

### 3. Browser-driven expiration
- The browser deletes both cookies automatically after TTL
- No server-side scheduler or refresh logic is required

### 4. Clean re-authentication
- Next user interaction triggers a new OAuth login
- Airflow generates a fresh JWT
- Roles are evaluated correctly

## Benefits of This Approach

- No Airflow code modification required
- No Keycloak refresh token dependency
- No infinite redirect or 403 loops
- Predictable user experience
- Security-friendly (short-lived sessions)
- Easy to audit and reason about

## Trade-offs

- Users must re-authenticate periodically (expected behavior)
- No “silent refresh” for UI sessions (not supported by Airflow)

This trade-off is acceptable and commonly adopted in production environments.

## Conclusion

Due to **intrinsic limitations of Apache Airflow 3.1.5**, reliable token refresh for browser-based sessions is not supported when using Keycloak with UMA disabled.

Managing session lifetime at the reverse proxy level is the **only stable and maintainable solution**.

This design ensures:
- Clean authentication state
- Correct authorization
- No manual cookie intervention
- Long-term operational stability
