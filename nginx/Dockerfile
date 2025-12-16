# Use your internal registry base image (already mirrored/allowed in your context)
FROM nginx:1.25-alpine

# Optional metadata
LABEL org.opencontainers.image.title="custom-nginx-static" \
      org.opencontainers.image.description="Static landing page for Airflow/Astronomer" \
      org.opencontainers.image.source="internal"

# Replace default nginx config with a minimal one (non-root, port 8080)
COPY nginx.conf /etc/nginx/nginx.conf

# Copy your static website
COPY index.html /usr/share/nginx/html/index.html

# Ensure permissions for non-root execution (nginx user exists on alpine image)
RUN mkdir -p /var/cache/nginx /var/run \
    && chown -R nginx:nginx /usr/share/nginx /var/cache/nginx /var/run /etc/nginx

USER nginx

EXPOSE 8080

# Basic healthcheck
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8080/ >/dev/null 2>&1 || exit 1
