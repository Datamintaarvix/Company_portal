"""Vercel Serverless Function entry point for DATAMINT AARVIX Enterprise HRMS."""

import sys
from pathlib import Path

# Ensure project root directory is in sys.path for serverless execution
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.main import app

# Vercel Python runtime targets the ASGI callable named 'app'
__all__ = ["app"]

