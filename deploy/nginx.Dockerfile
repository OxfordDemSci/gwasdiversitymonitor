# syntax=docker/dockerfile:1
FROM nginx:stable-alpine
ARG GWAS_BUILD_SHA
LABEL org.opencontainers.image.revision=$GWAS_BUILD_SHA
COPY app/static /var/www/app/static
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
