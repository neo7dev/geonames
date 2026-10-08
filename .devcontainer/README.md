# Geonames server

Geonames server is a project which serves a database for datasets available on geonames.org

Nginx and gostatic servers are used to serve the datasets (with configurable download speed for nginx from nginx.conf) to development environment.

Pgadmin is pgdamin, which connects to the database and allows various operations done on postgres database.

metabase is more analytical, it allows to generate specific maps from the data.

Do `make run` to get started.
