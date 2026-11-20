"""Keep the complete roster on the status message using pages when needed."""

from telethon.extensions import html

from .status import StatusMessage, StatusSection


def _fits(message: str) -> bool:
    text, entities = html.parse(message)
    return len(text.encode("utf-16-le")) <= 2 * 4096 and len(entities) <= 95


def status_pages(message: StatusMessage) -> tuple[str, ...]:
    rendered = str(message)
    if _fits(rendered):
        return (rendered,)

    sections = [message.header]
    pages = []
    for section in message.sections:
        current = []
        for block in section.blocks:
            candidate = str(StatusSection(section.title, (*current, block)))
            if not _fits("\n\n".join([*sections, candidate, message.footer])):
                if current:
                    sections.append(str(StatusSection(section.title, tuple(current))))
                pages.append("\n\n".join([*sections, message.footer]))
                sections = [message.header]
                current = []
            current.append(block)
        if not section.blocks and not _fits("\n\n".join([*sections, section.title, message.footer])):
            pages.append("\n\n".join([*sections, message.footer]))
            sections = [message.header]
        sections.append(str(StatusSection(section.title, tuple(current))))
    pages.append("\n\n".join([*sections, message.footer]))
    return tuple(pages)
