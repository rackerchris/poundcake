#!/bin/bash
# Create StackStorm database and user in MongoDB.
set -euo pipefail

mongo_user="${ST2_DB_USERNAME:-stackstorm}"
mongo_pass="${ST2_DB_PASSWORD:-stackstorm}"

mongosh --username "${MONGO_INITDB_ROOT_USERNAME}" --password "${MONGO_INITDB_ROOT_PASSWORD}" --authenticationDatabase admin <<EOS
use st2;
try {
  db.createUser({
    user: "${mongo_user}",
    pwd: "${mongo_pass}",
    roles: [{ role: "dbOwner", db: "st2" }]
  });
  print("StackStorm Mongo user created");
} catch (e) {
  if (e.codeName == "DuplicateKey") {
    db.updateUser("${mongo_user}", {
      pwd: "${mongo_pass}",
      roles: [{ role: "dbOwner", db: "st2" }]
    });
    print("StackStorm Mongo user updated");
  } else {
    throw e;
  }
}
EOS
