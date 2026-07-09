# Payer-Payee

ClaimsAI payer/payee dashboard built with React and Vite.

## Folder structure

- `frontend/` - React/Vite app, static assets, and generated frontend data.
- `backend/` - Express API, MongoDB connection, CSV import, and claim mapping code.
- `shared/` - Prediction logic used by both frontend and backend.
- `dist/` - Production frontend build output.

## Setup

```bash
npm install
npm run dev
```

## Backend

Copy `.env.example` to `.env`, set `MONGODB_URI`, then import the current CSV:

```bash
npm run import:mongo
```

Run the API and frontend together:

```bash
npm run dev:all
```

Run only the frontend:

```bash
npm run frontend
```

Run only the backend:

```bash
npm run backend
```

Backend API defaults to `http://127.0.0.1:4000`.

Prediction endpoints:

- `GET /api/predictions/dashboard`
- `GET /api/predictions/risk-queue`
- `GET /api/predictions/claims/:claimNumber`

## Checks

```bash
npm run lint
npm run build
```
