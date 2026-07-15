# syntax=docker/dockerfile:1

# --- Stage 1: build the React SPA -------------------------------------------
FROM node:20-alpine AS build
WORKDIR /app

# Install deps against the committed lockfile for reproducible builds.
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

# Build (tsc typecheck + vite bundle). Source is copied after deps so edits
# don't bust the dependency layer.
COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src
RUN npm run build

# --- Stage 2: serve static assets behind nginx + reverse proxy --------------
FROM nginx:1.27-alpine AS runtime

# The template is rendered by nginx's entrypoint (envsubst) using API_UPSTREAM /
# WEBAPP_UPSTREAM from the environment, then dropped into conf.d.
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html

# Defaults resolve the in-network doc-ray service names; override for split
# deployments where the API/webapp live elsewhere.
ENV API_UPSTREAM=http://api:8000 \
    WEBAPP_UPSTREAM=http://webapp:5001

EXPOSE 80
