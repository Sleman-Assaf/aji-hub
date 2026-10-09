# people/templatetags/people_tags.py
from django import template
from django.utils.html import format_html, mark_safe

register = template.Library()


@register.simple_tag
def emp_avatar(employee, size=44, font_size=None, classes=''):
    """
    Render an employee avatar: photo if uploaded, else coloured initials circle.
    Usage: {% emp_avatar emp %} or {% emp_avatar emp size=52 %}
    """
    if font_size is None:
        font_size = max(10, size // 3)

    base_style = (
        f'width:{size}px;height:{size}px;border-radius:50%;flex:none;'
        f'display:flex;align-items:center;justify-content:center;overflow:hidden;'
    )

    if employee and employee.photo:
        return format_html(
            '<div class="{cls}" style="{base}background:var(--paper-3)">'
            '<img src="{url}" alt="{name}" '
            'style="width:100%;height:100%;object-fit:cover;display:block">'
            '</div>',
            cls=classes,
            base=base_style,
            url=employee.photo.url,
            name=employee.full_name,
        )

    initials = employee.initials() if employee else '?'
    return format_html(
        '<div class="{cls}" style="{base}background:linear-gradient(135deg,var(--ink),#3a3a38);'
        'color:var(--yellow);font-weight:700;font-size:{fs}px">{ini}</div>',
        cls=classes,
        base=base_style,
        fs=font_size,
        ini=initials,
    )


@register.simple_tag
def user_avatar(user, size=44, font_size=None, classes=''):
    """
    Render a Django User avatar via their linked employee record.
    Usage: {% user_avatar request.user %}
    """
    try:
        emp = user.employee
        return emp_avatar(emp, size=size, font_size=font_size, classes=classes)
    except Exception:
        pass

    if font_size is None:
        font_size = max(10, size // 3)

    initials = (user.first_name[:1] + user.last_name[:1]).upper() or user.username[:2].upper()
    base_style = (
        f'width:{size}px;height:{size}px;border-radius:50%;flex:none;'
        f'display:flex;align-items:center;justify-content:center;overflow:hidden;'
    )
    return format_html(
        '<div class="{cls}" style="{base}background:linear-gradient(135deg,var(--ink),#3a3a38);'
        'color:var(--yellow);font-weight:700;font-size:{fs}px">{ini}</div>',
        cls=classes,
        base=base_style,
        fs=font_size,
        ini=initials,
    )
