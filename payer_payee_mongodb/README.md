# Local payer/payee MongoDB

This project runs a separate MongoDB instance bound only to the local device.

- Connection URI: `mongodb://127.0.0.1:27018/`
- Database: `payer_payee`
- Main collection (table): `837_claims`
- Patient primary-key collection: `patients` (`_id` = `Patient_ID`)
- Claim reference collection: `claim_registry` (`_id` = `Claim_ID`)

The main collection preserves all 145 spreadsheet headings exactly and adds `Patient_ID` plus `_source_row` for traceability. MongoDB has no native foreign-key constraint, so references are represented by matching values and verified with `$lookup` checks.

## Commands

```sh
./start_local_mongodb.sh
./.venv/bin/python verify_database.py
./.venv/bin/python connect_example.py
```

Stop only this isolated instance with:

```sh
./stop_local_mongodb.sh
```

The data files live under this project's `data/` directory and use port 27018, keeping them separate from any default MongoDB instance on port 27017.
