ROUTE_MAP = {
    "text": "text_ai",
    "image": "vision_ai",
    "audio": "audio_ai",
    "video": "video_ai",
    "spreadsheet": "table_ai",
    "document": "document_ai",
    "code": "code_ai",
    "unknown": "text_ai",
}


def route_analyzer(file_type: str) -> str:
    return ROUTE_MAP.get(file_type, "text_ai")
