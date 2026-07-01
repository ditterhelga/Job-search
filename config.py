import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
PROMPTS_DIR = ROOT_DIR / "prompts"

DATA_DIR.mkdir(exist_ok=True)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

MODE = os.environ.get("MODE", "ACTIVE_SEARCH")

# Keep these conservative while tuning. Increase later only if the report is too small.
MAX_ITEMS_PER_RSS_SOURCE = int(os.environ.get("MAX_ITEMS_PER_RSS_SOURCE", "25"))
MAX_ITEMS_PER_ATS_BOARD = int(os.environ.get("MAX_ITEMS_PER_ATS_BOARD", "50"))
MAX_AI_ANALYSIS_PER_RUN = int(os.environ.get("MAX_AI_ANALYSIS_PER_RUN", "15"))
MAX_RESULTS_TO_SEND = int(os.environ.get("MAX_RESULTS_TO_SEND", "5"))
MIN_INTERVIEW_CHANCE_TO_SEND = int(os.environ.get("MIN_INTERVIEW_CHANCE_TO_SEND", "55"))

# Outside NL, only remote roles should pass. This is an explicit user constraint.
REMOTE_ONLY_OUTSIDE_NL = os.environ.get("REMOTE_ONLY_OUTSIDE_NL", "true").lower() == "true"

# Source toggles.
USE_RSS = os.environ.get("USE_RSS", "true").lower() == "true"
USE_GREENHOUSE = os.environ.get("USE_GREENHOUSE", "true").lower() == "true"
USE_LEVER = os.environ.get("USE_LEVER", "true").lower() == "true"
USE_ASHBY = os.environ.get("USE_ASHBY", "true").lower() == "true"
USE_WTTJ = os.environ.get("USE_WTTJ", "false").lower() == "true"

# Storage files.
SEEN_JOBS_FILE = DATA_DIR / "seen_jobs.json"
FEEDBACK_FILE = DATA_DIR / "feedback.json"
TELEGRAM_OFFSET_FILE = DATA_DIR / "telegram_offset.json"
COMPANY_BOARDS_FILE = DATA_DIR / "company_boards.json"
