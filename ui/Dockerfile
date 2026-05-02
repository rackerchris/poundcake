# PoundCake UI Docker Image
#
# This Dockerfile builds the container image for PoundCake UI, which is deployed
# via the Helm chart. The image is published to GitHub Container Registry.
#
# Build: docker build -t ghcr.io/rackerlabs/poundcake-ui:VERSION -f ui/Dockerfile .
# Push:  docker push ghcr.io/rackerlabs/poundcake-ui:VERSION

ARG NODE_IMAGE=public.ecr.aws/docker/library/node:20-alpine
ARG NGINX_IMAGE=public.ecr.aws/docker/library/nginx:alpine

FROM ${NODE_IMAGE} AS build

WORKDIR /app/ui

COPY ui/package.json ui/package-lock.json* ./
RUN npm ci

COPY ui/index.html ui/tsconfig.json ui/tsconfig.node.json ui/vite.config.ts ./
COPY ui/src ./src
RUN npm run build

FROM ${NGINX_IMAGE}

# Copy the new React application.
COPY --from=build /app/ui/dist /usr/share/nginx/html

# Copy managed UI entrypoint.
COPY ui/docker-entrypoint.sh /app/ui/docker-entrypoint.sh
RUN chmod +x /app/ui/docker-entrypoint.sh

# Ensure nginx entrypoint and runtime paths are writable by non-root UID 1000.
RUN chown -R 1000:0 /etc/nginx/conf.d /var/cache/nginx /var/run /run

# Copy nginx configuration template (uses ${API_URL} placeholder).
# The repo-managed entrypoint renders this into /etc/nginx/conf.d/default.conf.
COPY ui/nginx/nginx-main.conf /etc/nginx/nginx.conf
COPY ui/nginx/nginx.conf /etc/nginx/templates/default.conf.template

# Default API URL - will be overridden by environment variable in Kubernetes
ENV API_URL=http://poundcake:8080
ENV UI_NGINX_ACCESS_LOG=off

# Expose port
EXPOSE 8080

# Labels
LABEL org.opencontainers.image.title="PoundCake UI" \
    org.opencontainers.image.description="Web UI for PoundCake auto-remediation framework" \
    org.opencontainers.image.authors="Jake Briggs" \
    org.opencontainers.image.source="https://github.com/rackerlabs/poundcake"

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://127.0.0.1:8080/ || exit 1

# Use repo-managed wrapper entrypoint to guarantee non-root-safe rendering.
ENTRYPOINT ["/app/ui/docker-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
