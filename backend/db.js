import { MongoClient } from 'mongodb'
import 'dotenv/config'

const uri = process.env.MONGODB_URI
const dbName = process.env.MONGODB_DB || 'payer_payee'

let client
let db

export async function connectMongo() {
  if (db) return db

  if (!uri) {
    throw new Error('MONGODB_URI is required. Add it to .env or export it before starting the backend.')
  }

  client = new MongoClient(uri)
  await client.connect()
  db = client.db(dbName)
  return db
}

export async function closeMongo() {
  if (client) {
    await client.close()
    client = undefined
    db = undefined
  }
}

export function getMongoConfig() {
  return {
    dbName,
    hasUri: Boolean(uri),
  }
}
