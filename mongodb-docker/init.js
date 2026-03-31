db = db.getSiblingDB("ETL_AIRBNB");

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