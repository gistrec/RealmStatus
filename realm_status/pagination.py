"""Keep the complete roster on the status message using pages when needed."""

from telethon.extensions import html


def _fits(message: str) -> bool:
    text, entities = html.parse(message)
    return len(text.encode("utf-16-le")) <= 2 * 4096 and len(entities) <= 95


def status_pages(message: str) -> tuple[str, ...]:
    if _fits(message):
        return (message,)

    header, *groups, footer = message.split("\n\n")
    sections = [header]
    pages = []
    for group in groups:
        title, *rows = group.splitlines()
        current = [title]
        for row in rows:
            candidate = "\n\n".join([*sections, "\n".join([*current, row]), footer])
            if not _fits(candidate):
                if len(current) > 1:
                    sections.append("\n".join(current))
                pages.append("\n\n".join([*sections, footer]))
                sections = [header]
                current = [title]
            current.append(row)
        if not rows and not _fits("\n\n".join([*sections, title, footer])):
            pages.append("\n\n".join([*sections, footer]))
            sections = [header]
        sections.append("\n".join(current))
    pages.append("\n\n".join([*sections, footer]))
    return tuple(pages)
