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

    sections: list[StatusSection] = []
    pages = []
    for section in message.sections:
        current = []
        for block in section.blocks:
            candidate = StatusSection(section.title, (*current, block))
            if not _fits(message.render_sections([*sections, candidate])):
                if current:
                    sections.append(StatusSection(section.title, tuple(current)))
                pages.append(message.render_sections(sections))
                sections = []
                current = []
            current.append(block)
        if not section.blocks and not _fits(message.render_sections([*sections, section])):
            pages.append(message.render_sections(sections))
            sections = []
        sections.append(StatusSection(section.title, tuple(current)))
    pages.append(message.render_sections(sections))
    return tuple(pages)
