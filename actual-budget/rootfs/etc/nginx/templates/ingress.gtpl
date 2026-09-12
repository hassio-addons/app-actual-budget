server {
    listen {{ .interface }}:{{ .port }} default_server;

    include /etc/nginx/includes/server_params.conf;
    include /etc/nginx/includes/proxy_params.conf;

    # Actual answers a request for a path it serves a directory from with a
    # redirect that adds a trailing slash. It builds that out of the request,
    # which arrives here with the Ingress path already stripped off.
    absolute_redirect off;
    proxy_redirect / $http_x_ingress_path/;

    # The client is built for the site root, which under Ingress is Home
    # Assistant's rather than Actual's, so every address it resolves from there
    # would land somewhere Actual never sees. The path it belongs under is
    # settled per request, and this is where it is written into the page.
    #
    # One tag, in one document. The client was patched at build time to state
    # its base and address everything relative to it, so this rewrite is the
    # whole of what changes per request: the bundles, the workers, the WASM
    # build of SQLite and the server the client syncs against all resolve
    # against the base on their own. The megabytes of JavaScript are untouched.
    sub_filter_once on;
    sub_filter '<base href="/">' '<base href="$http_x_ingress_path/">';

    location / {
        allow   172.30.32.2;
        deny    all;

        proxy_pass http://backend;
    }
}
