FROM nginx:alpine

RUN apk add --no-cache apache2-utils iproute2 su-exec \
    && mkdir -p /tmp/nginx/client_body /tmp/nginx/proxy /tmp/nginx/fastcgi /tmp/nginx/uwsgi /tmp/nginx/scgi \
    && chown -R nginx:nginx /tmp/nginx
COPY nginx.conf /etc/nginx/nginx.conf
COPY entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh
