"""
medo/models.py
Dataclasses / typed structures shared across all layers.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class AnswerSource(str, Enum):
    MARKER = "marker"
    COLOR = "color"
    BOTH = "both"
    UNKNOWN = "unknown"


class ExplanationStatus(str, Enum):
    FULL = "full"
    KEY_ONLY = "key_only"
    LOCKED = "locked"
    UNKNOWN = "unknown"


@dataclass
class ParsedOption:
    index: int          # 1-based
    text: str           # normalized plain text (from base64 ground truth)
    raw_html: str       # inner HTML of the label
    vote_percent: Optional[int] = None   # decoded from data-encoded-percent


@dataclass
class ParsedImage:
    original_url: str
    role: str           # "question" | "explanation" | "mindmap"
    # Filled later after download:
    sha1: Optional[str] = None
    local_path: Optional[str] = None
    mime: Optional[str] = None
    bytes_size: Optional[int] = None


@dataclass
class ParsedQuestion:
    # --- Identity ---
    source_url: str
    source_page: int                     # pagination page number
    ordinal: int                         # question number within topic

    # --- Content ---
    stem_html: str                       # sanitized HTML
    stem_text: str                       # plain text
    options: list[ParsedOption]          # 1-based, len 4 or 5

    # --- Answer ---
    correct_index: Optional[int]         # 1-based; None if unknown
    # The site's own question id (#question-meta).  It survives a stem edit,
    # a re-download through a different subtopic and a guest/login switch, so
    # it is the identity to use when the page exposed one.
    site_question_id: Optional[int] = None
    answer_source: AnswerSource = AnswerSource.UNKNOWN

    # --- Votes ---
    vote_percent: list[Optional[int]] = field(default_factory=list)

    # --- Explanation ---
    explanation_html: Optional[str] = None
    explanation_text: Optional[str] = None
    explanation_status: ExplanationStatus = ExplanationStatus.UNKNOWN
    # Texts of the hidden decoy blocks the page shipped, so a repair can tell
    # whether a legacy stored lesson still contains them.  Never persisted.
    decoy_texts: list[str] = field(default_factory=list)

    # --- Images ---
    images: list[ParsedImage] = field(default_factory=list)
    has_images: bool = False

    # --- Metadata ---
    content_hash: Optional[str] = None

    def compute_content_hash(self) -> str:
        from medo.text_utils import content_hash_input
        raw = content_hash_input(
            self.stem_text,
            [o.text for o in self.options],
        )
        self.content_hash = hashlib.sha256(raw.encode()).hexdigest()
        return self.content_hash

    def options_json(self) -> str:
        return json.dumps(
            [{"index": o.index, "text": o.text} for o in self.options],
            ensure_ascii=False,
        )

    def vote_percent_json(self) -> str:
        return json.dumps(
            {str(o.index): o.vote_percent for o in self.options},
            ensure_ascii=False,
        )


@dataclass
class Category:
    id: int
    parent_id: Optional[int]
    kind: str              # "category" | "lesson" | "section" | "topic"
    title: str
    source_url: str
    position: int
    children: list["Category"] = field(default_factory=list)
    question_count: int = 0
