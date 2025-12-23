FROM nginx:1.25-alpine

# Copier la config nginx
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copier la page HTML
COPY index.html /usr/share/nginx/html/

# ⭐ AJOUTE CETTE LIGNE - Override le pid pour utiliser /tmp
RUN sed -i 's|pid /var/run/nginx.pid;|pid /tmp/nginx.pid;|g' /etc/nginx/nginx.conf

# Permissions
RUN mkdir -p /var/cache/nginx /tmp \
    && chown -R nginx:nginx /usr/share/nginx /var/cache/nginx /etc/nginx /tmp \
    && chmod -R 755 /usr/share/nginx/html

USER nginx

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD wget -qO- http://127.0.0.1:8080/health >/dev/null 2>&1 || exit 1

