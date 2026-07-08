# Payer-Payee

ClaimsAI payer/payee dashboard built with React and Vite.

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

Backend API defaults to `http://127.0.0.1:4000`.

## Checks

```bash
npm run lint
npm run build
```
