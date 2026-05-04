from __future__ import annotations

import re
from dataclasses import dataclass

from shared.text_utils import normalize_ws


@dataclass(frozen=True)
class TextSegment:
    segment_id: str
    type: str
    start: int
    end: int
    text: str

    def as_dict(self) -> dict[str, object]:
        return {
            "segment_id": self.segment_id,
            "type": self.type,
            "start": self.start,
            "end": self.end,
            "text": self.text,
        }


NUMBERED_MARKER_RE = re.compile(r"(?m)^\s*(?:\(?\d+\)?[\).])\s+")
SUBJECT_LIST_RE = re.compile(
    r"\b(?:following\s+(?:refugee\s+)?slaves?|slaves?\s+whose\s+names?\s+are|for\s+delivery\s+to)\b",
    flags=re.I,
)


def build_segments(text: str) -> list[TextSegment]:
    source = text or ""
    segments: list[TextSegment] = []
    if not source.strip():
        return segments

    header_end = min(len(source), 300)
    segments.append(_segment("header", "header", 0, header_end, source))
    segments.extend(_paragraph_segments(source))
    segments.extend(_numbered_case_segments(source))
    segments.extend(_subject_list_segments(source))
    segments.extend(_line_segments(source))
    return _dedupe_segments(segments)


def containing_segments(segments: list[TextSegment], start: int, end: int, *types: str) -> list[TextSegment]:
    wanted = set(types)
    return [
        segment
        for segment in segments
        if (not wanted or segment.type in wanted) and segment.start <= start and segment.end >= end
    ]


def nearest_segment(segments: list[TextSegment], start: int, end: int, *types: str) -> TextSegment | None:
    containing = containing_segments(segments, start, end, *types)
    if containing:
        return min(containing, key=lambda segment: (segment.end - segment.start, segment.start))
    wanted = set(types)
    candidates = [segment for segment in segments if not wanted or segment.type in wanted]
    if not candidates:
        return None
    midpoint = (start + end) // 2
    return min(candidates, key=lambda segment: min(abs(segment.start - midpoint), abs(segment.end - midpoint)))


def expanded_window(text: str, start: int, end: int, *, before: int = 420, after: int = 520) -> TextSegment:
    source = text or ""
    win_start = max(0, start - before)
    win_end = min(len(source), end + after)
    win_start = _soft_left_boundary(source, win_start, start)
    win_end = _soft_right_boundary(source, end, win_end)
    return _segment("window", "expanded_window", win_start, win_end, source)


def _paragraph_segments(text: str) -> list[TextSegment]:
    segments: list[TextSegment] = []
    for index, match in enumerate(re.finditer(r"\S(?:.*?)(?=\n\s*\n|\Z)", text, flags=re.S), start=1):
        if normalize_ws(match.group(0)):
            segments.append(_segment(f"para_{index}", "paragraph", match.start(), match.end(), text))
    return segments


def _numbered_case_segments(text: str) -> list[TextSegment]:
    markers = list(NUMBERED_MARKER_RE.finditer(text))
    segments: list[TextSegment] = []
    for index, marker in enumerate(markers, start=1):
        next_start = markers[index].start() if index < len(markers) else len(text)
        end = _trim_segment_end(text, marker.start(), next_start)
        if end > marker.start():
            segments.append(_segment(f"case_{index}", "numbered_case", marker.start(), end, text))
    return segments


def _subject_list_segments(text: str) -> list[TextSegment]:
    segments: list[TextSegment] = []
    for index, match in enumerate(SUBJECT_LIST_RE.finditer(text), start=1):
        start = max(0, match.start() - 80)
        end_candidates = [len(text), match.end() + 900]
        for stopper in re.finditer(r"\n\s*\n|(?:^|\n)\s*(?:i\s+request|these\s+slaves|this\s+slave|yours|signed)\b", text[match.end() :], flags=re.I):
            end_candidates.append(match.end() + stopper.start())
            break
        end = min(end_candidates)
        segments.append(_segment(f"subject_list_{index}", "subject_list_block", start, end, text))
    return segments


def _line_segments(text: str) -> list[TextSegment]:
    segments: list[TextSegment] = []
    offset = 0
    index = 1
    for line in text.splitlines(keepends=True):
        start = offset
        end = offset + len(line)
        if normalize_ws(line):
            segments.append(_segment(f"line_{index}", "line", start, end, text))
            index += 1
        offset = end
    return segments


def _segment(segment_id: str, kind: str, start: int, end: int, text: str) -> TextSegment:
    return TextSegment(segment_id=segment_id, type=kind, start=start, end=end, text=normalize_ws(text[start:end]))


def _dedupe_segments(segments: list[TextSegment]) -> list[TextSegment]:
    seen: set[tuple[str, int, int]] = set()
    result: list[TextSegment] = []
    for segment in sorted(segments, key=lambda item: (item.start, item.end, item.type)):
        key = (segment.type, segment.start, segment.end)
        if key in seen or not segment.text:
            continue
        seen.add(key)
        result.append(segment)
    return result


def _trim_segment_end(text: str, start: int, proposed_end: int) -> int:
    blank = re.search(r"\n\s*\n", text[start:proposed_end])
    if blank and blank.start() > 40:
        return start + blank.start()
    return proposed_end


def _soft_left_boundary(text: str, proposed: int, hard_end: int) -> int:
    for pattern in ("\n\n", ". ", "; "):
        pos = text.rfind(pattern, proposed, hard_end)
        if pos >= 0:
            return pos + len(pattern)
    return proposed


def _soft_right_boundary(text: str, hard_start: int, proposed: int) -> int:
    for pattern in ("\n\n", ". ", "; "):
        pos = text.find(pattern, hard_start, proposed)
        if pos >= 0 and pos > hard_start:
            return pos + len(pattern)
    return proposed
