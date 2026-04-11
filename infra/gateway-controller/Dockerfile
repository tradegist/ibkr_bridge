FROM docker:27-cli AS docker-cli

FROM alpine:3.20
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
RUN apk add --no-cache busybox-extras && mkdir -p /var/www/cgi-bin
COPY start-gateway.sh /var/www/cgi-bin/start-gateway
COPY gateway-status.sh /var/www/cgi-bin/gateway-status
RUN chmod +x /var/www/cgi-bin/*
EXPOSE 9000
CMD ["/usr/sbin/httpd", "-f", "-p", "9000", "-h", "/var/www"]
