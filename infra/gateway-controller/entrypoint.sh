#!/bin/sh
monitor-gateway &
exec /usr/sbin/httpd -f -p 9000 -h /var/www
