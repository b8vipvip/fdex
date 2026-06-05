def as_markdown_download(content: str) -> str:
    return content if content.endswith("\n") else content + "\n"
