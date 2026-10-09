"""Things about this project that `manage.py check` should refuse to let through.

Only faults that are silent in development and visible to everybody in
production earn a place here.
"""
import pathlib
import re

from django.conf import settings
from django.core.checks import Error, register

#: `{#` … `#}`, and whether the two ended up on the same line.
COMMENT_OPEN = '{#'
COMMENT_CLOSE = '#}'


@register()
def multiline_template_comments(app_configs, **kwargs):
    """A Django template comment must fit on one line, or it is not a comment.

    `{# … #}` is matched by a regex without re.DOTALL, so a comment broken across
    two lines is never recognised as one. Django prints it — brackets, hashes and
    all — into the page, and it appears on screen to every reader.

    Nothing about writing one looks wrong, the templates are not compiled until
    they are rendered, and the text lands wherever the comment happened to be, so
    it is easy to add and easy to miss. This has reached the live Hub three times:
    once across four templates, then on the homepage, then above the booth upload
    box. Hence a check rather than another sweep.

    Write them stacked instead:

        {# the first line #}
        {# the second line #}
    """
    problems = []
    roots = [pathlib.Path(d) for d in settings.TEMPLATES[0].get('DIRS', [])]
    roots = [r for r in roots if r.exists()] or [pathlib.Path(settings.BASE_DIR) / 'templates']

    for root in roots:
        for path in sorted(root.rglob('*.html')):
            try:
                text = path.read_text(encoding='utf-8', errors='replace')
            except OSError:
                continue
            at = 0
            while True:
                start = text.find(COMMENT_OPEN, at)
                if start == -1:
                    break
                end = text.find(COMMENT_CLOSE, start + 2)
                if end == -1:
                    problems.append((path, text.count('\n', 0, start) + 1,
                                     'is never closed'))
                    break
                if '\n' in text[start + 2:end]:
                    problems.append((path, text.count('\n', 0, start) + 1,
                                     'runs onto the next line'))
                at = end + 2

    return [
        Error(
            f'Template comment {why}, so Django will print it onto the page.',
            hint='Django comments are single-line. Stack one {# … #} per line.',
            obj=f'{path}:{line}',
            id='core.E001',
        )
        for path, line, why in problems
    ]
