import cors from 'cors'
import 'dotenv/config'
import express from 'express'
import { closeMongo, connectMongo, getMongoConfig } from './db.js'

const app = express()
const port = Number(process.env.PORT || 4000)
const corsOrigin = process.env.CORS_ORIGIN || 'http://127.0.0.1:5173'

app.use(cors({ origin: corsOrigin }))
app.use(express.json())

function escapeRegex(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function pageOptions(query) {
  const page = Math.max(Number.parseInt(query.page, 10) || 1, 1)
  const limit = Math.min(Math.max(Number.parseInt(query.limit, 10) || 25, 1), 100)
  return { page, limit, skip: (page - 1) * limit }
}

function buildClaimQuery(query) {
  const filters = {}

  if (query.search) {
    const regex = new RegExp(escapeRegex(String(query.search).trim()), 'i')
    filters.$or = [
      { patient: regex },
      { memberId: regex },
      { number: regex },
      { claimId: regex },
    ]
  }

  if (query.payer && query.payer !== 'All Payers') filters.payer = query.payer
  if (query.plan && query.plan !== 'All Plans') filters.filingIndicator = query.plan
  if (query.providerGroup && query.providerGroup !== 'All Groups') filters.billingProvider = query.providerGroup
  if (query.status) filters.status = new RegExp(escapeRegex(String(query.status)), 'i')

  if (query.from || query.to) {
    filters.dos = {}
    if (query.from) filters.dos.$gte = query.from
    if (query.to) filters.dos.$lte = query.to
  }

  return filters
}

function financialSummaryStage() {
  return {
    _id: null,
    totalClaims: { $sum: 1 },
    totalCharges: { $sum: '$totalCharge' },
    totalAllowed: { $sum: '$allowed' },
    totalPaid: { $sum: '$paid' },
    totalPatientResp: { $sum: '$patientResp' },
    totalAdjustment: { $sum: '$adjustment' },
    deniedClaims: {
      $sum: {
        $cond: [{ $eq: ['$status', 'Denied'] }, 1, 0],
      },
    },
  }
}

app.get('/health', async (_req, res, next) => {
  try {
    const db = await connectMongo()
    await db.command({ ping: 1 })
    res.json({ ok: true, mongo: getMongoConfig() })
  } catch (error) {
    next(error)
  }
})

app.get('/api/claims', async (req, res, next) => {
  try {
    const db = await connectMongo()
    const query = buildClaimQuery(req.query)
    const { page, limit, skip } = pageOptions(req.query)
    const [items, total] = await Promise.all([
      db.collection('claims')
        .find(query)
        .sort({ dos: -1, claimId: 1 })
        .skip(skip)
        .limit(limit)
        .toArray(),
      db.collection('claims').countDocuments(query),
    ])

    res.json({ page, limit, total, items })
  } catch (error) {
    next(error)
  }
})

app.get('/api/claims/:claimNumber', async (req, res, next) => {
  try {
    const db = await connectMongo()
    const claimNumber = req.params.claimNumber
    const claim = await db.collection('claims').findOne({
      $or: [{ number: claimNumber }, { claimId: claimNumber }],
    })

    if (!claim) {
      res.status(404).json({ message: 'Claim not found' })
      return
    }

    res.json(claim)
  } catch (error) {
    next(error)
  }
})

app.get('/api/members', async (req, res, next) => {
  try {
    const db = await connectMongo()
    const { page, limit, skip } = pageOptions(req.query)
    const query = {}

    if (req.query.search) {
      const regex = new RegExp(escapeRegex(String(req.query.search).trim()), 'i')
      query.$or = [
        { patient: regex },
        { memberId: regex },
      ]
    }

    const [items, total] = await Promise.all([
      db.collection('members')
        .find(query)
        .sort({ latestServiceDate: -1, memberId: 1 })
        .skip(skip)
        .limit(limit)
        .toArray(),
      db.collection('members').countDocuments(query),
    ])

    res.json({ page, limit, total, items })
  } catch (error) {
    next(error)
  }
})

app.get('/api/members/:memberId', async (req, res, next) => {
  try {
    const db = await connectMongo()
    const member = await db.collection('members').findOne({ memberId: req.params.memberId })

    if (!member) {
      res.status(404).json({ message: 'Member not found' })
      return
    }

    res.json(member)
  } catch (error) {
    next(error)
  }
})

app.get('/api/members/:memberId/claims', async (req, res, next) => {
  try {
    const db = await connectMongo()
    const items = await db.collection('claims')
      .find({ memberId: req.params.memberId })
      .sort({ dos: -1, claimId: 1 })
      .toArray()

    res.json({ total: items.length, items })
  } catch (error) {
    next(error)
  }
})

app.get('/api/dashboard', async (req, res, next) => {
  try {
    const db = await connectMongo()
    const query = buildClaimQuery(req.query)
    const collection = db.collection('claims')

    const [
      summary,
      recentClaims,
      payers,
      plans,
      providerGroups,
    ] = await Promise.all([
      collection.aggregate([{ $match: query }, { $group: financialSummaryStage() }]).toArray(),
      collection.find(query).sort({ dos: -1, claimId: 1 }).limit(10).toArray(),
      collection.distinct('payer'),
      collection.distinct('filingIndicator'),
      collection.distinct('billingProvider'),
    ])

    res.json({
      summary: summary[0] || {
        totalClaims: 0,
        totalCharges: 0,
        totalAllowed: 0,
        totalPaid: 0,
        totalPatientResp: 0,
        totalAdjustment: 0,
        deniedClaims: 0,
      },
      recentClaims,
      filters: {
        payers: payers.filter(Boolean).sort(),
        plans: plans.filter(Boolean).sort(),
        providerGroups: providerGroups.filter(Boolean).sort(),
      },
    })
  } catch (error) {
    next(error)
  }
})

app.use((error, _req, res, _next) => {
  const status = error.status || 500
  const message = status === 500 ? 'Internal server error' : error.message
  console.error(error)
  res.status(status).json({ message })
})

const server = app.listen(port, () => {
  console.log(`ClaimsAI backend listening on http://127.0.0.1:${port}`)
})

async function shutdown() {
  server.close(async () => {
    await closeMongo()
    process.exit(0)
  })
}

process.on('SIGINT', shutdown)
process.on('SIGTERM', shutdown)
