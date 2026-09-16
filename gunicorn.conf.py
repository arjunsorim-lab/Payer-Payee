"""Production defaults, including services with a saved Render start command."""

import os

bind = f"0.0.0.0:{os.getenv('PORT', '4000')}"
workers = 1
worker_class = "gthread"
threads = 4
timeout = 120
preload_app = True
accesslog = "-"
errorlog = "-"
