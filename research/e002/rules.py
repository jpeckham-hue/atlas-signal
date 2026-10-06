"""Frozen Run 1 configuration for Experiment 002.

Every value here comes from the "Frozen Run 1 configuration" table and the
lexicons in docs/experiments/002-event-relationship-baseline.md (design commit
1592ca8). Changing any value after Run 1 has been scored is tuning on the
evaluation set and requires a new run version.

Ratio thresholds are Fractions so boundary comparisons are exact.
"""

from fractions import Fraction

RUN = "run1"

CORPUS_SHA256 = "5bb7742f07dfcc495cb050414ae6c7a74b0f546e8578b9aff9c89ccfbe689dfd"

# --- Frozen threshold table -------------------------------------------------

TITLE_WEIGHT = 2
NUM_MAX_DF = 5
WRAPPER_MIN_SHARE = Fraction("0.30")
BOILERPLATE_MIN_TOKENS = 12
BOILERPLATE_MIN_ARTICLES = 2
SOURCE_COMMON_SHARE = Fraction("0.20")
TEMPLATE_MIN_SHARED = Fraction("0.50")
CANDIDATE_MAX_DAYS = 14
CAND_COSINE = Fraction("0.15")
CAND_TOP_K = 5
CAND_TOP_K_MIN = Fraction("0.08")
CAND_ENTITY_MAX_DF = 5
CAND_TAG_MAX_DF = 8
CAND_ISSUER_HOURS = 24
COPY_TITLE_JACCARD = Fraction("0.90")
COPY_SUMMARY_JACCARD = Fraction("0.80")
COPY_MIN_SUMMARY_CHARS = 80
COPY_MAX_HOURS = 48
COMPANION_MAX_MINUTES = 60
COMPANION_MIN_COSINE = Fraction("0.15")
ADVISORY_MAX_DAYS = 7
ADVISORY_MIN_ANCHORS = 2
ADVISORY_MAX_ENTITY_ANCHORS = 1
ADVISORY_MIN_COSINE = Fraction("0.25")
SAME_EVENT_MAX_HOURS = 72
SAME_EVENT_COSINE = Fraction("0.35")
SAME_EVENT_NUMBER_COSINE = Fraction("0.15")
SAME_EVENT_TITLE_COSINE = Fraction("0.50")
CLUSTER_MIN_CROSS_COSINE = Fraction("0.15")
CONTAINER_CLAUSE_COSINE = Fraction("0.40")
PART_OF_ENABLED = False
FOLLOWS_FROM_BASIS = ("publisher_stated",)
DISTINCT_RULES = ("template_slot_conflict",)

# --- Lexicons used by corpus.py and text.py ---------------------------------

# Section 1: document-type vocabulary supplied by source categories.
DOCUMENT_TYPES = (
    "news releases",
    "media advisories",
    "backgrounders",
    "readouts",
    "statements",
)

# Section 1: the issuing unit is the path segment after one of these language
# segments on this publisher domain.
ISSUING_UNIT_DOMAIN = "canada.ca"
ISSUING_UNIT_LANGUAGE_SEGMENTS = ("en", "fr")

# Section 4: catch-all categories excluded from the tag signal (pre-measurement
# clarification recorded in the design's "Freeze and versioning" section).
CATCH_ALL_CATEGORIES = ("POLICY_AREA=GENINFO",)

# Sections 1, 7 and 8: format classes from URL path segments, in precedence
# order (the first class with a matching segment wins).
FORMAT_URL_SEGMENTS = (
    ("live", ("live",)),
    ("programme", ("sounds",)),
    ("audio", ("audio", "podcast", "podcasts")),
    ("video", ("video", "videos")),
    ("opinion", ("commentisfree", "opinion")),
)

# Section 7: live-blog title suffixes. Matched only after a spaced dash.
LIVE_TITLE_SUFFIXES = ("as it happened", "business live", "live")

FORMAT_CLASSES = ("live", "programme", "audio", "video", "opinion", "standard")

# Section 7: container markers. URL path segments, the live-blog title
# suffixes above, and titles consisting only of a recognised date plus
# generic round-up words.
CONTAINER_URL_SEGMENTS = ("live", "sounds")
ROUNDUP_WORDS = ("daily", "news", "briefing", "round-up")

# Section 2: numbers.
CURRENCY_SYMBOLS = ("£", "$", "€", "¥")
MAGNITUDES = {
    "k": 1_000,
    "thousand": 1_000,
    "m": 1_000_000,
    "million": 1_000_000,
    "bn": 1_000_000_000,
    "billion": 1_000_000_000,
}
YEAR_MIN = 1900
YEAR_MAX = 2100
SMALL_INTEGER_LIMIT = 100

# Section 2: plural stripping keeps at least this many characters.
PLURAL_MIN_STEM = 4

MONTH_NAMES = (
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
)
MONTH_ABBREVIATIONS = (
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept",
    "oct", "nov", "dec",
)
WEEKDAY_NAMES = (
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
    "sunday",
)

# Sentence splitting: a period after one of these does not end a sentence.
ABBREVIATIONS = (
    "mr", "mrs", "ms", "dr", "prof", "st", "jr", "sr", "no", "vs", "etc",
    "inc", "ltd", "co", "corp", "gov", "sen", "rep", "hon",
)

# Section 2: generic English function words only. No domain words.
STOPWORDS = frozenset("""
a about above after again against all also am an and any are as at
be because been before being below between both but by
can could
d did do does doing don down during
each either
few for from further
had has have having he her here hers herself him himself his how
i if in into is it its itself
just
ll
m me more most my myself
neither no nor not now
o of off on once only or other our ours ourselves out over own
re
s same she should so some such
t than that the their theirs them themselves then there these they this
those through to too
under until up upon
ve very
was we were what when where whether which while who whom whose why will
with within without would
you your yours yourself yourselves
""".split())
