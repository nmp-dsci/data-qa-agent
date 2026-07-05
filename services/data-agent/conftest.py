"""Ensure the service root is importable as the `agent` package.

Kept at the service root so both pytest and the sandbox's spawned child process
(which inherits sys.path) can `import agent...`.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
