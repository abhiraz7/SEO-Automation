"""
"Worth It" scoring -- turns raw metrics (volume, KD, intent, competition,
SERP features) into the verdict a marketing worker actually needs: is this
keyword worth chasing, on a 0-10 scale, with a plain-language explanation.

Deterministic heuristic, no ML and no extra API calls: everything scored here
is data we already fetched. SERP features (AI Overview, ads) sharpen the score
when available but are optional -- with DataForSEO unverified they're simply
unknown and the score says so in its factors.

The weights are a judgment call, not a measured model. Tune them against real
client outcomes once the agency has some -- they're all in one place on purpose.
"""
import math

from .schemas import WorthIt

# Score starts from volume potential, then difficulty and SERP crowding
# subtract from it. Bands drive the traffic-light badge in the UI.
EASY_THRESHOLD = 7.5
AVOID_THRESHOLD = 4.0

_INTENT_VALUE = {
    # How likely a visit from this intent turns into business for a client.
    "transactional": 1.0,
    "commercial": 0.85,
    "local": 1.0,
    "informational": 0.55,
    "navigational": 0.25,  # searcher wants a specific brand -- hard to intercept
}


def _volume_component(volume: int | None) -> tuple[float, str]:
    """0-4 points on a log scale: 10 -> ~0.7, 1k -> ~2, 100k -> ~3.3, 1M -> 4."""
    if not volume:
        return 0.0, "No search volume data — treat as unproven demand"
    points = min(4.0, math.log10(volume) * (4.0 / 6.0))
    if volume < 50:
        label = f"Very low volume ({volume}/mo)"
    elif volume < 1000:
        label = f"Modest volume ({volume}/mo)"
    else:
        label = f"Strong volume ({volume:,}/mo)"
    return points, label


def _difficulty_component(difficulty: int | None) -> tuple[float, str]:
    """0-4 points, linear: KD 0 -> 4 points, KD 100 -> 0. Unknown KD sits in
    the middle rather than pretending it's easy."""
    if difficulty is None:
        return 2.0, "Difficulty unknown — assume a medium fight"
    points = 4.0 * (1 - difficulty / 100)
    if difficulty <= 30:
        label = f"Low difficulty (KD {difficulty}) — rankable with on-page work"
    elif difficulty <= 60:
        label = f"Medium difficulty (KD {difficulty}) — needs good content + some links"
    else:
        label = f"High difficulty (KD {difficulty}) — established sites dominate"
    return points, label


def _intent_component(intent: str | None) -> tuple[float, str]:
    """0-2 points for how commercially useful the click is."""
    if not intent:
        return 1.0, "Intent unknown"
    value = _INTENT_VALUE.get(intent, 0.6)
    labels = {
        "transactional": "Transactional intent — searcher is ready to act",
        "commercial": "Commercial intent — comparing options, good lead potential",
        "local": "Local intent — high conversion for local businesses",
        "informational": "Informational intent — traffic, but slower to convert",
        "navigational": "Navigational intent — searcher wants a specific site",
    }
    return 2.0 * value, labels.get(intent, f"{intent.title()} intent")


def _serp_component(serp_features: dict | None) -> tuple[float, list[str]]:
    """0 to -2.5 penalty for SERP furniture that eats organic clicks. Only
    applied when a real SERP was inspected -- absence of data is not absence
    of ads."""
    if serp_features is None:
        return 0.0, ["SERP features not checked yet — score may drop once inspected"]
    penalty = 0.0
    notes: list[str] = []
    if serp_features.get("ai_overview"):
        penalty += 1.2
        notes.append("AI Overview present — expect fewer organic clicks")
    ads = serp_features.get("ads", 0)
    if ads:
        penalty += min(0.8, 0.2 * ads)
        notes.append(f"{ads} ads above/around results")
    if serp_features.get("featured_snippet"):
        penalty += 0.3
        notes.append("Featured snippet occupies position zero")
    if serp_features.get("local_pack"):
        penalty += 0.2
        notes.append("Map pack present — local listings matter as much as ranking")
    if not notes:
        notes.append("Clean SERP — organic results get the clicks")
    return -penalty, notes


def score_keyword(
    volume: int | None,
    difficulty: int | None,
    intent: str | None,
    serp_features: dict | None = None,
) -> WorthIt:
    vol_pts, vol_note = _volume_component(volume)
    kd_pts, kd_note = _difficulty_component(difficulty)
    intent_pts, intent_note = _intent_component(intent)
    serp_pts, serp_notes = _serp_component(serp_features)

    score = max(0.0, min(10.0, vol_pts + kd_pts + intent_pts + serp_pts))
    band = "easy" if score >= EASY_THRESHOLD else ("avoid" if score < AVOID_THRESHOLD else "medium")

    return WorthIt(
        score=round(score, 1),
        band=band,
        factors=[vol_note, kd_note, intent_note, *serp_notes],
    )
