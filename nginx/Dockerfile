FROM nginx:1.25-alpine

# Copier le template nginx (avec la variable)
COPY nginx.conf /etc/nginx/templates/default.conf.template

# Copier la page HTML
COPY index.html /usr/share/nginx/html/

# FIX COMPLET pour les permissions
RUN set -x \
    # Changer le pid dans le fichier principal
    && sed -i 's|pid /var/run/nginx.pid;|pid /tmp/nginx.pid;|g' /etc/nginx/nginx.conf \
    # Créer les dossiers nécessaires
    && mkdir -p /var/cache/nginx /tmp /var/log/nginx \
    # Donner les permissions à nginx
    && chown -R nginx:nginx /usr/share/nginx /var/cache/nginx /etc/nginx /tmp /var/log/nginx \
    && chmod -R 755 /usr/share/nginx/html \
    # Donner les permissions sur /var/run (au cas où)
    && mkdir -p /var/run \
    && chown -R nginx:nginx /var/run \
    # Vérifier que le fichier nginx.conf a bien été modifié
    && grep -q '/tmp/nginx.pid' /etc/nginx/nginx.conf || (echo "ERROR: pid not changed" && exit 1)

USER nginx

EXPOSE 8080

# Health check qui teste /tmp au lieu de /var/run
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD wget -qO- http://127.0.0.1:8080/health >/dev/null 2>&1 || exit 1
