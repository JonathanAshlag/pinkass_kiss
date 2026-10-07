"""
Plain-text processors: any textual file the KB can read as UTF-8.

`.txt`-like prose is stored verbatim. Code and data files (`.json`, `.js`, `.py`, ...) are
wrapped in a fenced code block so markdown chunking and rendering keep them intact. The
file content is still the whole original, so nothing is retained in the blob store.
"""

from pathlib import Path

from kb.ingest.processors.base import ProcessedDocument

PROSE_EXTENSIONS = frozenset({".txt", ".text", ".rst", ".log"})

# extension -> fence language ("" = no language tag)
CODE_LANGUAGES = {
    ".json": "json", ".jsonl": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
    ".xml": "xml", ".ini": "ini", ".cfg": "ini", ".env": "",
    ".js": "javascript", ".mjs": "javascript", ".jsx": "jsx", ".ts": "typescript", ".tsx": "tsx",
    ".py": "python", ".java": "java", ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp",
    ".cs": "csharp", ".go": "go", ".rs": "rust", ".rb": "ruby", ".php": "php",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash", ".sql": "sql", ".css": "css",
    ".swift": "swift", ".kt": "kotlin", ".scala": "scala", ".r": "r", ".lua": "lua",
}


def _fence_for(text: str) -> str:
    """A backtick fence longer than any backtick run inside `text`."""
    longest = run = 0
    for ch in text:
        run = run + 1 if ch == "`" else 0
        longest = max(longest, run)
    return "`" * max(3, longest + 1)


class PlainTextProcessor:
    name = "text"
    extensions = PROSE_EXTENSIONS

    def process(self, path: Path) -> ProcessedDocument:
        content = path.read_text(encoding="utf-8")  # UnicodeDecodeError -> reported as failed
        if not content.strip():
            raise ValueError("file is empty")
        return ProcessedDocument(title=path.stem, content=content)


class CodeProcessor:
    name = "code"
    extensions = frozenset(CODE_LANGUAGES)

    def process(self, path: Path) -> ProcessedDocument:
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            raise ValueError("file is empty")
        fence = _fence_for(text)
        lang = CODE_LANGUAGES[path.suffix.lower()]
        body = text if text.endswith("\n") else text + "\n"
        return ProcessedDocument(
            title=path.name, content=f"{fence}{lang}\n{body}{fence}\n"
        )
