"""Serve disposable synthetic scale data on loopback for browser measurements.

Install backend/requirements-dev.txt and build the frontend first.
This exercises directory loading, not prediction throughput.
"""
import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--port', type=int, default=4002)
parser.add_argument('--claims', type=int, default=100000)
args = parser.parse_args()
if args.claims < 100000:
    parser.error('Scale verification requires at least 100,000 claims.')

with tempfile.TemporaryDirectory(prefix='payer-scale-') as directory:
    os.environ['PRELOAD_WORKBOOK'] = 'false'
    os.environ['REVIEW_DB_PATH'] = str(Path(directory) / 'reviews.sqlite3')
    from backend.tests.test_scale_readiness import _ScaleDatabase
    import backend.app as application
    database = _ScaleDatabase(args.claims)
    application.configured_workbook_database = lambda: database
    application.app.config.update(REVIEW_AUTH_REQUIRED=False)
    application.app.run(host='127.0.0.1', port=args.port, threaded=True, use_reloader=False)
