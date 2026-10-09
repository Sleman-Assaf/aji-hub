# cms/richtext.py
"""Sanitising for text edited in place.

Rich text means an editor's markup is stored and then rendered for every employee,
so it cannot be trusted the way an SVG icon or a page title can. This keeps a
short allow-list and drops everything else — tags, attributes and URL schemes.

Deliberately narrow. The formatting people actually reach for in a paragraph is
bold, italic, a link and a list; a full HTML editor here would mean styles,
tables, iframes and a much larger surface to police. Anything beyond this belongs
in the Custom HTML block, which already requires an administrator to approve it.
"""
import re

#: Tags kept. Everything else is unwrapped — its text survives, the tag does not,
#: so removing a stray <div> never silently deletes a paragraph of content.
ALLOWED = {
    'b', 'strong', 'i', 'em', 'u', 's', 'br', 'p', 'a',
    'ul', 'ol', 'li', 'code',
}

#: The only attribute worth keeping, and only on a link.
ALLOWED_ATTRS = {'a': {'href', 'title'}}

#: Tags whose content is not text and must go entirely, not be unwrapped.
DROP_CONTENT = {'script', 'style', 'iframe', 'object', 'embed', 'svg', 'math',
                'form', 'input', 'button', 'select', 'textarea'}

_TAG = re.compile(r'<(/?)\s*([a-zA-Z0-9]+)((?:\s+[^<>]*?)?)\s*(/?)>', re.S)
_ATTR = re.compile(r'([a-zA-Z-]+)\s*=\s*("[^"]*"|\'[^\']*\'|[^\s"\'>]+)')
_SAFE_HREF = re.compile(r'^(?:https?://|mailto:|tel:|/|\#)', re.I)

#: Rough cap. Inline text is a paragraph, not a document; a runaway paste should
#: not become a row the size of a page.
MAX_LENGTH = 20000


def _clean_attrs(tag, raw):
    keep = []
    for name, value in _ATTR.findall(raw or ''):
        name = name.lower()
        if name not in ALLOWED_ATTRS.get(tag, set()):
            continue
        value = value.strip('"\'').strip()
        if name == 'href':
            # A javascript: or data: link in stored content is a script that runs
            # for whoever clicks it.
            if not _SAFE_HREF.match(value):
                continue
        if '<' in value or '"' in value:
            continue
        keep.append(f'{name}="{value}"')
    return (' ' + ' '.join(keep)) if keep else ''


def sanitise_html(markup):
    """Reduce edited markup to the allow-list. Never raises."""
    if not markup:
        return ''
    text = markup[:MAX_LENGTH]

    # Remove elements whose contents are not displayable text at all.
    for tag in DROP_CONTENT:
        text = re.sub(rf'<{tag}\b.*?</{tag}\s*>', '', text, flags=re.S | re.I)
        text = re.sub(rf'<{tag}\b[^>]*/?>', '', text, flags=re.I)

    # Strip comments, which can hide conditional markup.
    text = re.sub(r'<!--.*?-->', '', text, flags=re.S)

    out, stack = [], []
    pos = 0
    for m in _TAG.finditer(text):
        out.append(text[pos:m.start()])
        pos = m.end()
        closing, tag, attrs, self_close = m.group(1), m.group(2).lower(), m.group(3), m.group(4)

        if tag not in ALLOWED:
            continue                      # unwrap: the text inside is kept

        if tag == 'br' or self_close:
            out.append('<br>' if tag == 'br' else f'<{tag}{_clean_attrs(tag, attrs)}>')
            continue

        if closing:
            if tag in stack:
                # Close anything opened inside it, so the result nests correctly.
                while stack:
                    open_tag = stack.pop()
                    out.append(f'</{open_tag}>')
                    if open_tag == tag:
                        break
            continue

        stack.append(tag)
        out.append(f'<{tag}{_clean_attrs(tag, attrs)}>')

    out.append(text[pos:])
    while stack:
        out.append(f'</{stack.pop()}>')

    cleaned = ''.join(out)
    # A block holding nothing but empty markup should read as empty, so the
    # template can fall back to its placeholder.
    if not re.sub(r'<[^>]+>|&nbsp;|\s', '', cleaned):
        return ''
    return cleaned


def looks_like_html(text):
    """Whether stored text already carries markup.

    Bodies written before inline editing existed are plain text with newlines, and
    those still need paragraphing on the way out.
    """
    return bool(text) and bool(re.search(r'</?(?:p|br|ul|ol|li|b|strong|i|em|a)\b',
                                         text, re.I))
