"""Engine matcher — selects the best engine for a given target difficulty.

Uses weighted scoring across capability dimensions to rank available
engines and select the optimal one for the target.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from apexcrawler.engines.base import EngineCapability
from apexcrawler.routing.registry import EngineRegistry


@dataclass
class MatchResult:
    """Result of an engine matching operation."""

    engine_name: str
    score: float
    reasons: list[str] = field(default_factory=list)


class EngineMatcher:
    """Matches targets to the best available engine based on capability scoring.

    The matcher evaluates each registered engine against a set of weighted
    criteria derived from the target's difficulty level and required features.
    """

    # Default weights for each capability dimension
    DEFAULT_WEIGHTS: dict[str, float] = {
        "fingerprint_resistance": 0.30,
        "ja4_diversity": 0.20,
        "dom_automation": 0.15,
        "resource_cost": 0.10,
        "supports_webgpu": 0.10,
        "supports_wasm_intercept": 0.10,
        "supports_cdp_hide": 0.05,
    }

    def __init__(
        self,
        weights: dict[str, float] | None = None,
        require_tags: list[str] | None = None,
    ) -> None:
        """Initialize the matcher.

        Args:
            weights: Custom weights for capability dimensions.
            require_tags: Tags that an engine MUST have to be considered.
        """
        self._weights = weights or dict(self.DEFAULT_WEIGHTS)
        self._require_tags = require_tags or []

    def match(
        self,
        difficulty: int | None = None,
        min_score: float = 0.0,
        require_features: dict[str, bool] | None = None,
    ) -> list[MatchResult]:
        """Score all registered engines and return ranked results.

        Args:
            difficulty: Target difficulty (1-10). Higher difficulty prioritizes
                        stealth over cost. If None, uses balanced weights.
            min_score: Minimum score threshold for inclusion.
            require_features: Dict of feature booleans the engine must support
                              (e.g. {"supports_webgpu": True}).

        Returns:
            List of MatchResult sorted by score descending.
        """
        require_features = require_features or {}
        weights = self._compute_weights(difficulty)
        results: list[MatchResult] = []

        for name, cap in EngineRegistry.list_capabilities().items():
            # Filter by required tags
            if self._require_tags and not all(
                tag in cap.tags for tag in self._require_tags
            ):
                continue

            # Filter by required features
            if not self._check_features(cap, require_features):
                continue

            score, reasons = self._score_engine(cap, weights)
            if score >= min_score:
                results.append(
                    MatchResult(engine_name=name, score=score, reasons=reasons)
                )

        results.sort(key=lambda r: r.score, reverse=True)
        return results

    def best_engine(
        self,
        difficulty: int | None = None,
        require_features: dict[str, bool] | None = None,
    ) -> MatchResult | None:
        """Return the single best engine match.

        Args:
            difficulty: Target difficulty level.
            require_features: Required engine features.

        Returns:
            The top MatchResult, or None if no engine matches.
        """
        results = self.match(difficulty=difficulty, require_features=require_features)
        return results[0] if results else None

    # ── Private helpers ──────────────────────────────────────

    def _compute_weights(self, difficulty: int | None) -> dict[str, float]:
        """Adjust weights based on target difficulty.

        As difficulty increases, stealth dimensions (fingerprint_resistance,
        ja4_diversity, cdp_hide) get higher weight, while resource_cost gets
        lower weight (high-cost engines become acceptable).
        """
        if difficulty is None:
            return dict(self._weights)

        diff_factor = min(difficulty / 10.0, 1.0)  # 0.0 to 1.0

        adjusted = dict(self._weights)
        # Amplify stealth-related weights with difficulty
        adjusted["fingerprint_resistance"] = self._weights.get("fingerprint_resistance", 0.30) * (1.0 + diff_factor)
        adjusted["ja4_diversity"] = self._weights.get("ja4_diversity", 0.20) * (1.0 + diff_factor)
        adjusted["supports_cdp_hide"] = self._weights.get("supports_cdp_hide", 0.05) * (1.0 + diff_factor * 2)
        # Reduce cost sensitivity at high difficulty
        adjusted["resource_cost"] = self._weights.get("resource_cost", 0.10) * (1.0 - diff_factor * 0.5)
        # Dom automation is less relevant for high-difficulty targets
        adjusted["dom_automation"] = self._weights.get("dom_automation", 0.15) * (1.0 - diff_factor * 0.3)

        # Normalize weights to sum to 1.0
        total = sum(adjusted.values())
        if total > 0:
            adjusted = {k: v / total for k, v in adjusted.items()}

        return adjusted

    def _score_engine(
        self, cap: EngineCapability, weights: dict[str, float]
    ) -> tuple[float, list[str]]:
        """Score a single engine's capability against the given weights.

        Returns (score, reasons) where reasons are strings explaining
        significant scoring factors.
        """
        score = 0.0
        reasons: list[str] = []

        # Score numeric dimensions (1-10 scale, normalize to 0-1)
        for dim in ("fingerprint_resistance", "ja4_diversity", "dom_automation"):
            value = getattr(cap, dim, 5)
            normalized = (value - 1) / 9.0  # Map 1-10 to 0-1
            weight = weights.get(dim, 0.0)
            score += normalized * weight
            if value >= 8:
                reasons.append(f"Strong {dim.replace('_', ' ')} ({value}/10)")

        # Resource cost — lower is better (1 is cheapest, 10 is most expensive)
        cost = getattr(cap, "resource_cost", 5)
        cost_score = 1.0 - (cost - 1) / 9.0  # Invert: low cost = high score
        score += cost_score * weights.get("resource_cost", 0.0)
        if cost <= 3:
            reasons.append(f"Low resource cost ({cost}/10)")

        # Boolean features
        bool_features = ("supports_webgpu", "supports_wasm_intercept", "supports_cdp_hide")
        for feat in bool_features:
            if getattr(cap, feat, False):
                score += weights.get(feat, 0.0)
                reasons.append(f"{feat.replace('_', ' ').title()} enabled")

        return score, reasons

    def _check_features(
        self, cap: EngineCapability, required: dict[str, bool]
    ) -> bool:
        """Check if an engine satisfies all required feature constraints."""
        for feature, expected in required.items():
            if getattr(cap, feature, False) != expected:
                return False
        return True
