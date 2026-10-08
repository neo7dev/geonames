# Geonames server

This would be a geonames server for all the datasets available on geonames.org. It would be a 1:1 mapping of the tab seperated txt file to postgres database tables. It would have serve a fastapi endpoint, different endpoints for each table.

## What to do

1. Download the dataset
2. Extract the dataset
3. Insert all the records into the postgres database for each dataset in separate tables
4. Optimize postgres database
5. Db interaction layer
6. Fast api - api layer

Later:

1. Add download dataset method which downloads and extracts the datasets, does not remove them
2. If the server is up continously then only process changes
3. Add redis caching
4. Add force rebuild


## Download module

1. 