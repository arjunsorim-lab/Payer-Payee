import fs from 'node:fs/promises'
import path from 'node:path'
import { parse } from 'csv-parse/sync'
import 'dotenv/config'
import { buildMemberDocuments, normalizeClaim } from './claimMapper.js'
import { closeMongo, connectMongo, getMongoConfig } from './db.js'
import { predictClaim } from '../shared/predictionEngine.js'

const defaultCsvPath = '/Users/user/Downloads/EDI_834_837_20 members(837_Claims).csv'
const csvPath = process.argv[2] || process.env.CSV_PATH || defaultCsvPath

function chunk(items, size) {
  const chunks = []
  for (let index = 0; index < items.length; index += size) {
    chunks.push(items.slice(index, index + size))
  }
  return chunks
}

async function ensureIndexes(db) {
  const claims = db.collection('claims')
  const members = db.collection('members')
  const predictions = db.collection('claim_predictions')

  await claims.createIndex({ claimId: 1 }, { unique: true })
  await claims.createIndex({ number: 1 }, { unique: true })
  await claims.createIndex({ memberId: 1, dos: -1 })
  await claims.createIndex({ payer: 1, dos: -1 })
  await claims.createIndex({ billingProvider: 1, dos: -1 })
  await claims.createIndex({ patient: 'text', memberId: 'text', number: 'text' })

  await members.createIndex({ memberId: 1 }, { unique: true })
  await members.createIndex({ patient: 'text', memberId: 'text' })

  await predictions.createIndex({ claimId: 1 }, { unique: true })
  await predictions.createIndex({ number: 1 }, { unique: true })
  await predictions.createIndex({ memberId: 1, dos: -1 })
  await predictions.createIndex({ 'prediction.risks.overall.score': -1 })
}

async function bulkUpsert(collection, documents, keyField) {
  let modified = 0
  let upserted = 0

  for (const batch of chunk(documents, 500)) {
    if (!batch.length) continue

    const result = await collection.bulkWrite(
      batch.map((document) => ({
        replaceOne: {
          filter: { [keyField]: document[keyField] },
          replacement: document,
          upsert: true,
        },
      })),
      { ordered: false },
    )

    modified += result.modifiedCount
    upserted += result.upsertedCount
  }

  return { modified, upserted }
}

async function main() {
  const resolvedCsvPath = path.resolve(csvPath)
  const csv = await fs.readFile(resolvedCsvPath, 'utf8')
  const rows = parse(csv, {
    columns: true,
    skip_empty_lines: true,
    trim: true,
  })

  const claims = rows
    .map(normalizeClaim)
    .filter((claim) => claim.claimId && claim.memberId)
    .sort((a, b) => b.dos.localeCompare(a.dos))
  const members = buildMemberDocuments(claims)

  const db = await connectMongo()
  await ensureIndexes(db)

  const claimResult = await bulkUpsert(db.collection('claims'), claims, 'claimId')
  const memberResult = await bulkUpsert(db.collection('members'), members, 'memberId')
  const predictionDocs = claims.map((claim) => ({
    claimId: claim.claimId,
    number: claim.number,
    memberId: claim.memberId,
    patient: claim.patient,
    payer: claim.payer,
    billingProvider: claim.billingProvider,
    dos: claim.dos,
    prediction: predictClaim(claim, claims),
    predictedAt: new Date(),
  }))
  const predictionResult = await bulkUpsert(db.collection('claim_predictions'), predictionDocs, 'claimId')

  if (process.env.SYNC_DELETE === 'true') {
    await db.collection('claims').deleteMany({ claimId: { $nin: claims.map((claim) => claim.claimId) } })
    await db.collection('members').deleteMany({ memberId: { $nin: members.map((member) => member.memberId) } })
  }

  const totals = await db.collection('claims').aggregate([
    {
      $group: {
        _id: null,
        totalClaims: { $sum: 1 },
        totalCharges: { $sum: '$totalCharge' },
        totalAllowed: { $sum: '$allowed' },
        totalPaid: { $sum: '$paid' },
        totalPatientResp: { $sum: '$patientResp' },
      },
    },
  ]).toArray()

  console.log(JSON.stringify({
    database: getMongoConfig().dbName,
    csvPath: resolvedCsvPath,
    parsedRows: rows.length,
    importedClaims: claims.length,
    importedMembers: members.length,
    claimResult,
    memberResult,
    predictionResult,
    totals: totals[0] || {},
  }, null, 2))
}

main()
  .catch((error) => {
    console.error(error.message)
    process.exitCode = 1
  })
  .finally(async () => {
    await closeMongo()
  })
