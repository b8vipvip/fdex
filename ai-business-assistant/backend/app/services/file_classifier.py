from pathlib import Path

EXTENSION_MAP = {
    "text": {".txt", ".md", ".log", ".conf", ".ini", ".yaml", ".yml"},
    "image": {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".svg"},
    "audio": {".mp3", ".wav", ".m4a", ".aac", ".ogg"},
    "video": {".mp4", ".mov", ".avi", ".mkv", ".webm"},
    "spreadsheet": {".xls", ".xlsx", ".csv"},
    "document": {".pdf", ".doc", ".docx"},
    "code": {".py", ".js", ".ts", ".tsx", ".jsx", ".php", ".java", ".go", ".html", ".css", ".json", ".rs", ".sql"},
    "archive": {".zip", ".rar", ".7z", ".tar", ".gz"},
}

MIME_PREFIX_MAP = {
    "image/": "image",
    "audio/": "audio",
    "video/": "video",
    "text/": "text",
}


def classify_file(filename: str, mime_type: str | None = None) -> str:
    normalized_name = filename.lower()
    if normalized_name.endswith(".env.example"):
        return "text"
    ext = Path(filename).suffix.lower()
    for file_type, extensions in EXTENSION_MAP.items():
        if ext in extensions:
            return file_type
    if mime_type:
        for prefix, file_type in MIME_PREFIX_MAP.items():
            if mime_type.startswith(prefix):
                return file_type
        if mime_type in {"application/pdf", "application/msword"}:
            return "document"
    return "unknown"
