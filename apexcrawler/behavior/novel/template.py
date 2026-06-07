"""
Novel reader behavior template for web novel platforms (Qidian, etc.)
Simulates a real reader's browsing and reading patterns.
"""

from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ReaderProfile(Enum):
    """Three reader archetypes with different reading speeds and behaviors."""
    ENGAGED = "engrossed"    # Immersed reader, 400-500 wpm
    SKIMMER = "skimmer"      # Fast reader, skips filler, 600-800 wpm
    CASUAL = "relaxed"       # Casual reader, frequent breaks, 300-400 wpm


@dataclass
class NovelReadingConfig:
    """Configuration for novel reading behavior."""
    profile: ReaderProfile = ReaderProfile.ENGAGED
    chapter_word_count: int = 3000      # Average chapter length
    chapters_per_session: int = 8       # Chapters per reading session
    max_session_minutes: int = 120      # Max session length
    min_session_minutes: int = 30       # Min session length
    pause_between_chapters: tuple = (5, 15)  # Seconds between chapters
    probability_backtrack: float = 0.15  # Chance to re-read prev chapter
    probability_break: float = 0.2       # Chance to take a mid-session break


class NovelReaderTemplate:
    """
    Behavior template simulating a real web novel reader.

    Stages:
    1. enter_catalog()    — Browse chapter list
    2. select_chapter()   — Click to open chapter
    3. read_chapter()     — Read content with scrolling
    4. transition()       — Move to next chapter
    5. session_end()      — Finish reading session
    """

    def __init__(self, config: Optional[NovelReadingConfig] = None):
        self.config = config or NovelReadingConfig()

    def calculate_chapter_dwell(self, word_count: int) -> float:
        """
        Calculate how long to spend reading a chapter.

        Reading speed varies by profile:
        - Engaged: 400-500 wpm (immersive reading)
        - Skimmer: 600-800 wpm (fast reading, skipping filler)
        - Casual: 300-400 wpm (slow, frequent pauses)

        Adds attention decay (readers get slower over time) and
        random buffer (30-120s) for distractions.
        """
        wpm_map = {
            ReaderProfile.ENGAGED: random.randint(400, 500),
            ReaderProfile.SKIMMER: random.randint(600, 800),
            ReaderProfile.CASUAL: random.randint(300, 400),
        }
        wpm = wpm_map[self.config.profile]
        base_time = (word_count / wpm) * 60  # seconds
        attention_decay = 1.0 + (random.random() * 0.15)  # 1.0 to 1.15
        buffer = random.randint(30, 120)
        return base_time * attention_decay + buffer

    def get_page_transition_delay(self) -> float:
        """Delay between finishing one chapter and starting next."""
        low, high = self.config.pause_between_chapters
        delay = random.uniform(low, high)
        # Occasionally add a longer pause (bathroom break, etc.)
        if random.random() < 0.1:
            delay += random.uniform(30, 180)
        return delay

    def should_backtrack(self) -> bool:
        """Whether to re-read the previous chapter to confirm details."""
        return random.random() < self.config.probability_backtrack

    def get_scroll_intervals(self, content_height_px: int) -> list[float]:
        """
        Generate realistic scroll intervals for reading content.

        Readers scroll in bursts, with pauses at paragraph boundaries.
        Returns list of pause durations (seconds) between scroll actions.
        """
        num_paragraphs = max(1, content_height_px // 80)  # ~80px per paragraph
        pauses = []
        for i in range(num_paragraphs):
            # Base pause: time to read one paragraph
            base = random.uniform(2.0, 6.0)
            # Occasional longer pause at dramatic moments
            if random.random() < 0.08:
                base += random.uniform(3.0, 10.0)
            pauses.append(round(base, 1))
        return pauses

    def get_session_duration(self) -> tuple[float, float]:
        """
        Calculate session start and end behavior.
        Returns (session_length_minutes, chapters_this_session).
        """
        session_min = random.uniform(self.config.min_session_minutes,
                                      self.config.max_session_minutes)
        chapters = max(3, int(session_min / 5))  # Rough estimate
        chapters = min(chapters, self.config.chapters_per_session)
        return session_min, chapters


# Utility functions
def estimate_chapter_word_count(html_text: str) -> int:
    """Estimate word count from HTML text content."""
    text = re.sub(r'<[^>]+>', '', html_text)
    # Chinese text: count characters (excluding whitespace/punctuation)
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
    return chinese_chars
