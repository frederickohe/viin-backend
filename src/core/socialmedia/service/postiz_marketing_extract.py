"""
Extract marketing copy and media URLs from Postiz Public API create-post payloads.

See https://docs.postiz.com/public-api/posts/create — posts[].value[] blocks carry
`content` and `image` (and sometimes `video`).
"""

from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple

# Canonical agent name stored in DB; callers may send common typos.
DIGITAL_MARKETING_AGENT_ALIASES: Set[str] = {
    "digital_marketing",
    "digital_margeting",
    "digital-marketing",
}


def normalize_digital_marketing_agent_name(raw: str | None) -> str | None:
    if raw is None:
        return None
    key = raw.strip().lower().replace(" ", "_").replace("-", "_")
    if key in DIGITAL_MARKETING_AGENT_ALIASES:
        return "digital_marketing"
    return None


def _append_text(parts: List[str], value: Any) -> None:
    if isinstance(value, str) and value.strip():
        parts.append(value.strip())


def _collect_media_urls(node: Any, out: List[str], seen: Set[str]) -> None:
    if node is None:
        return
    if isinstance(node, str):
        s = node.strip()
        if s.startswith("http://") or s.startswith("https://"):
            if s not in seen:
                seen.add(s)
                out.append(s)
        return
    if isinstance(node, list):
        for x in node:
            _collect_media_urls(x, out, seen)
        return
    if isinstance(node, dict):
        for k in ("url", "src", "path", "link", "href"):
            if k in node:
                _collect_media_urls(node.get(k), out, seen)
        return


def extract_marketing_text_and_links(payload: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Return combined caption/marketing text and ordered media URLs."""
    texts: List[str] = []
    links: List[str] = []
    seen: Set[str] = set()

    posts = payload.get("posts")
    if isinstance(posts, list):
        for p in posts:
            if not isinstance(p, dict):
                continue
            val = p.get("value")
            if isinstance(val, list):
                for block in val:
                    if not isinstance(block, dict):
                        continue
                    _append_text(texts, block.get("content"))
                    _collect_media_urls(block.get("image"), links, seen)
                    _collect_media_urls(block.get("video"), links, seen)

    if not texts:
        for key in ("content", "text", "message", "caption"):
            _append_text(texts, payload.get(key))

    combined = "\n\n".join(texts) if texts else ""
    return combined, links
