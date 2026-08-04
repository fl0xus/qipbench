"""Service-wide paths and limits, all overridable via environment variables."""

import os
from pathlib import Path

YASOL_BIN = Path(os.environ.get("YASOL_BIN", "/usr/local/bin/yasol"))
YASOL_INI = Path(os.environ.get("YASOL_INI", "/opt/yasol/Yasol.ini"))
PROVENANCE_FILE = Path(os.environ.get("YASOL_PROVENANCE", "/opt/yasol/solver-provenance.txt"))
JULIA_BIN = Path(os.environ.get("JULIA_BIN", "/usr/local/bin/julia"))
JULIA_ENV = Path(os.environ.get("JULIA_PROJECT", "/opt/julia-env"))
EXAMPLES_DIR = Path(os.environ.get("YASOL_EXAMPLES", "/opt/yasol-examples"))

WORK_ROOT = Path(os.environ.get("YASOL_WORK_ROOT", "/work"))

# Wall-clock ceiling per request. The solver's own --timeLimit is a soft limit it
# checks between search nodes, so the hard kill has to sit above it.
MAX_TIMEOUT_SECONDS = int(os.environ.get("YASOL_MAX_TIMEOUT", "600"))
DEFAULT_TIME_LIMIT_SECONDS = int(os.environ.get("YASOL_DEFAULT_TIME_LIMIT", "60"))
TIMEOUT_GRACE_SECONDS = int(os.environ.get("YASOL_TIMEOUT_GRACE", "15"))

MAX_UPLOAD_BYTES = int(os.environ.get("YASOL_MAX_UPLOAD_BYTES", str(16 * 1024 * 1024)))
MAX_OUTPUT_CHARS = int(os.environ.get("YASOL_MAX_OUTPUT_CHARS", str(256 * 1024)))
