db = db.getSiblingDB("ETL_AIRBNB");

// crear usuario
db.createUser({
  user: "AIRBNB",
  pwd: "12345",
  roles: [
    {
      role: "readWrite",
      db: "ETL_AIRBNB"
    }
  ]
});

// crear colecciones
db.createCollection("calendar");
db.createCollection("listings");
db.createCollection("reviews");