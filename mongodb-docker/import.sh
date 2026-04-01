#!/bin/bash

echo "Importando archivos CSV..."

mongoimport --username admin --password 123456 --authenticationDatabase admin \
  --db ETL_AIRBNB --collection calendar --type csv --headerline --file /data/docs/calendar.csv

mongoimport --username admin --password 123456 --authenticationDatabase admin \
  --db ETL_AIRBNB --collection listings --type csv --headerline --file /data/docs/listings.csv

mongoimport --username admin --password 123456 --authenticationDatabase admin \
  --db ETL_AIRBNB --collection reviews --type csv --headerline --file /data/docs/reviews.csv

echo "Importación terminada"