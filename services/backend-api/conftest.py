"""Ensure the service root is importable as the `app` package.

Mirrors services/data-agent/conftest.py: pytest inserts this directory into
sys.path so the service tests can `import app...` without the service being an
installed package ([tool.uv] package = false).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
