db = db.getSiblingDB("ETL_AIRBNB");

<<<<<<< HEAD
// crear usuario
=======
>>>>>>> feature--Exraction-class
db.createUser({
  user: "AIRBNB",
  pwd: "12345",
  roles: [
    {
      role: "readWrite",
      db: "ETL_AIRBNB"
    }
  ]
<<<<<<< HEAD
});

// crear colecciones
db.createCollection("calendar");
db.createCollection("listings");
db.createCollection("reviews");
=======
});
>>>>>>> feature--Exraction-class
