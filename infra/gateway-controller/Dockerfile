FROM docker:27-cli AS docker-cli

FROM alpine:3.23
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
RUN apk add --no-cache busybox-extras curl jq && mkdir -p /var/www/cgi-bin
COPY start-gateway.sh /var/www/cgi-bin/start-gateway
COPY gateway-status.sh /var/www/cgi-bin/gateway-status
COPY monitor-gateway.sh /usr/local/bin/monitor-gateway
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /var/www/cgi-bin/* /usr/local/bin/monitor-gateway /entrypoint.sh
EXPOSE 9000
CMD ["/entrypoint.sh"]
